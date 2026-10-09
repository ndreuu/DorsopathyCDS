"""Bounded, verified file exchange shared by process adapters and workers."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .contracts import ContractError, loads_json, validate_request, validate_result


MAX_JSON_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_DERIVED_FIELDS = {"artifact_id", "relative_path", "sha256", "size_bytes", "media_type", "role"}


def safe_exchange_path(exchange_dir, relative_path):
    """Resolve a regular exchange path without accepting any symlink component."""
    if (
        type(relative_path) is not str or not relative_path
        or relative_path.startswith("/") or "\\" in relative_path or ":" in relative_path
        or any(ord(char) < 32 or ord(char) == 127 for char in relative_path)
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ContractError("Worker exchange requires a safe relative path")
    root = Path(exchange_dir)
    if root.is_symlink() or not root.is_dir():
        raise ContractError("Worker exchange directory is unsafe")
    current = root
    for part in relative_path.split("/"):
        current = current / part
        if current.is_symlink():
            raise ContractError("Worker exchange paths cannot contain symlinks")
    return current


def _read_json(path):
    descriptor = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON_BYTES:
            raise ContractError("Worker JSON exceeds the supported file profile")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_JSON_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(data) > MAX_JSON_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ContractError("Worker JSON changed or exceeded its limit")
        return loads_json(data)
    except OSError:
        raise ContractError("Worker exchange JSON cannot be read") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_json(path, payload):
    try:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        loads_json(data)
    except (TypeError, ValueError, RecursionError):
        raise ContractError("Worker exchange requires finite JSON") from None
    if len(data) > MAX_JSON_BYTES:
        raise ContractError("Worker JSON exceeds its size limit")
    temporary = path.parent / (".pending-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_exchange_file(exchange_dir, relative_path, sha256, size_bytes):
    path = safe_exchange_path(exchange_dir, relative_path)
    descriptor = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size_bytes:
            raise ContractError("Worker artifact size or file profile is invalid")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        if size != size_bytes or digest.hexdigest() != sha256 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ContractError("Worker artifact integrity check failed")
        return path
    except OSError:
        raise ContractError("Worker artifact cannot be read safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _request_envelope(value, exchange_dir):
    if type(value) is not dict or set(value) != {"schema_version", "request", "inputs"} or value["schema_version"] != "1.0":
        raise ContractError("Invalid worker request envelope")
    request = validate_request(value["request"])
    inputs = value["inputs"]
    if type(inputs) is not list:
        raise ContractError("Worker inputs must be a list")
    references = {ref["artifact_id"]: ref for ref in request["input_artifacts"]}
    seen = set()
    for item in inputs:
        if type(item) is not dict or set(item) != {"artifact_id", "relative_path"}:
            raise ContractError("Invalid worker input descriptor")
        artifact_id = item["artifact_id"]
        if type(artifact_id) is not str or artifact_id not in references or artifact_id in seen:
            raise ContractError("Worker inputs do not match request artifacts")
        reference = references[artifact_id]
        if item["relative_path"] != "inputs/" + reference["sha256"]:
            raise ContractError("Worker input path is not canonical")
        verify_exchange_file(exchange_dir, item["relative_path"], reference["sha256"], reference["size_bytes"])
        seen.add(artifact_id)
    if seen != set(references):
        raise ContractError("Worker input mapping is incomplete")
    return deepcopy(value)


def write_worker_request(exchange_dir, request, inputs):
    payload = {"schema_version": "1.0", "request": request, "inputs": inputs}
    payload = _request_envelope(payload, exchange_dir)
    _write_json(safe_exchange_path(exchange_dir, "request.json"), payload)


def read_worker_request(exchange_dir):
    """Return validated {schema_version, request, inputs}; paths remain relative."""
    return _request_envelope(_read_json(safe_exchange_path(exchange_dir, "request.json")), exchange_dir)


def _output_envelope(value, exchange_dir, request):
    if type(value) is not dict or set(value) != {"schema_version", "result", "derived"} or value["schema_version"] != "1.0":
        raise ContractError("Invalid worker output envelope")
    result = validate_result(value["result"], request)
    derived = value["derived"]
    if type(derived) is not list:
        raise ContractError("Worker derived artifacts must be a list")
    declared = {ref["artifact_id"]: ref for ref in result["artifacts"]}
    sources = {ref["artifact_id"]: ref for ref in request["input_artifacts"]}
    seen = set()
    for item in derived:
        if type(item) is not dict or set(item) != _DERIVED_FIELDS:
            raise ContractError("Invalid worker derived descriptor")
        if type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            raise ContractError("Worker derived size must be a non-negative integer")
        artifact_id = item["artifact_id"]
        if type(artifact_id) is not str or artifact_id not in declared or artifact_id in seen or artifact_id in sources:
            raise ContractError("Worker derived artifact identity is invalid")
        reference = declared[artifact_id]
        if (
            reference["role"] != "derived" or item["role"] != "derived"
            or artifact_id != "artifact-" + reference["sha256"]
            or reference["storage_key"] != "artifacts/" + reference["sha256"]
            or any(item[field] != reference[field] for field in ("sha256", "size_bytes", "media_type", "role"))
        ):
            raise ContractError("Worker derived artifact metadata is invalid")
        verify_exchange_file(exchange_dir, item["relative_path"], item["sha256"], item["size_bytes"])
        seen.add(artifact_id)
    for artifact_id, reference in declared.items():
        if artifact_id in sources:
            if reference != sources[artifact_id]:
                raise ContractError("Worker cannot alter source artifact metadata")
        elif artifact_id not in seen:
            raise ContractError("Worker result contains an undeclared file")
    return {"schema_version": "1.0", "result": result, "derived": deepcopy(derived)}


def write_worker_output(exchange_dir, result, derived):
    """Verify and atomically publish output.json; result remains ModuleResult 1.0."""
    request = read_worker_request(exchange_dir)["request"]
    payload = _output_envelope({"schema_version": "1.0", "result": result, "derived": derived}, exchange_dir, request)
    _write_json(safe_exchange_path(exchange_dir, "output.json"), payload)


def read_worker_output(exchange_dir, request):
    return _output_envelope(_read_json(safe_exchange_path(exchange_dir, "output.json")), exchange_dir, request)
