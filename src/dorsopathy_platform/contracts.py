"""Strict wire contracts for independent, non-diagnostic analysis modules.

These validators preserve explicit unknown/not-applicable states and raw
measurement quality.  They perform no clinical interpretation or coercion.
Identifiers must be opaque ASCII tokens: syntax checks cannot detect whether
an otherwise valid token contains patient information.

JSON Schemas in ``schemas/v1`` describe structural constraints.  These Python
validators additionally check finite JSON values, unique identifiers,
cross-artifact identity, evidence resolution, and request/result context.
"""

from copy import deepcopy
from datetime import datetime
import json
import math
import re
from typing import Any, Dict, Optional, Union


SCHEMA_VERSION = "1.0"
_MAX_JSON_DEPTH = 64


class ContractError(ValueError):
    """Invalid module wire payload; messages contain field paths, not values."""


_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
_CONCEPT_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+\Z", re.ASCII
)
_HASH_PATTERN = re.compile(r"[A-Fa-f0-9]{64}\Z", re.ASCII)
_REQUEST_FIELDS = {
    "schema_version", "job_id", "episode_id", "module_id", "module_version",
    "input_artifacts", "parameters", "protocol_id",
}
_RESULT_FIELDS = {
    "schema_version", "result_id", "job_id", "episode_id", "module_id",
    "module_version", "status", "observations", "artifacts", "warnings", "error",
}
_ARTIFACT_FIELDS = {
    "artifact_id", "storage_key", "sha256", "media_type", "size_bytes", "role",
}
_OBSERVATION_FIELDS = {
    "observation_id", "concept", "assessment_status", "value", "unit", "anatomy",
    "quality", "origin", "method", "evidence_refs", "model", "acquired_at",
    "coordinate_frame",
}
_ANATOMY_FIELDS = {"region", "side", "level", "numbering_status"}
_MODEL_FIELDS = {"id", "version", "weights_sha256"}
_CONTEXT_FIELDS = ("job_id", "episode_id", "module_id", "module_version")


def _fail(path: str, message: str) -> None:
    raise ContractError("{}: {}".format(path, message))


def _object(value: Any, path: str, required: Optional[set] = None) -> dict:
    if type(value) is not dict or any(type(key) is not str for key in value):
        _fail(path, "must be a JSON object with string keys")
    if required is not None and set(value) != required:
        _fail(path, "requires exactly the documented fields")
    return value


def _array(value: Any, path: str) -> list:
    if type(value) is not list:
        _fail(path, "must be a JSON array")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or _ID_PATTERN.fullmatch(value) is None or value in {".", ".."}:
        _fail(path, "must be an opaque ASCII identifier of 1 to 100 characters")
    return value


def _enum(value: Any, allowed: set, path: str) -> str:
    if type(value) is not str or value not in allowed:
        _fail(path, "must be one of the documented values")
    return value


def _hash(value: Any, path: str) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        _fail(path, "must be a 64-character hexadecimal SHA256")
    return value


def _acquisition_time(value: Any, path: str) -> None:
    """Validate known source-acquisition time without inventing a timezone."""
    if value is None:
        return
    _string(value, path)
    timestamp = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        _fail(path, "must be a timezone-aware ISO8601 source-acquisition timestamp or null")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(path, "must include the source-acquisition timezone")


def _json(value: Any, path: str, active: Optional[set] = None, depth: int = 0) -> None:
    """Accept finite JSON, rejecting cycles and nesting beyond 64 edges."""
    if depth > _MAX_JSON_DEPTH:
        _fail(path, "JSON nesting exceeds the supported limit of 64")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail(path, "number must be finite")
        return
    if type(value) not in (list, dict):
        _fail(path, "must contain only JSON values")
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        _fail(path, "cyclic values are not JSON")
    active.add(identity)
    try:
        if type(value) is list:
            for index, item in enumerate(value):
                _json(item, "{}[{}]".format(path, index), active, depth + 1)
        else:
            _object(value, path)
            # Do not echo caller-supplied keys, which might contain identifying
            # information, in error messages for generic JSON objects.
            for item in value.values():
                _json(item, path + ".value", active, depth + 1)
    finally:
        active.remove(identity)


def _reject_json_constant(value: str) -> None:
    _fail("json", "non-finite JSON constants are not supported")


def _unique_json_object(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("json", "duplicate object keys are not supported")
        value[key] = item
    return value


def loads_json(text: Union[str, bytes]) -> Any:
    """Decode unambiguous finite JSON with a safe, bounded nesting depth.

    Envelope-specific validators remain necessary after decoding.  Errors do
    not disclose supplied field names or values.  Callers reading files or
    HTTP bodies should enforce their own byte-size limits before this call.
    """
    if type(text) not in (str, bytes):
        _fail("json", "input must be text or bytes")
    try:
        value = json.loads(
            text, parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except ContractError:
        raise
    except (ValueError, RecursionError):
        raise ContractError("json: cannot be decoded safely") from None
    _json(value, "json")
    return value


def _storage_key(value: Any, path: str) -> str:
    _string(value, path)
    if (
        value.startswith("/") or "\\" in value or ":" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(component in {"", ".", ".."} for component in value.split("/"))
    ):
        _fail(path, "must be a safe relative POSIX storage key")
    return value


def _artifact(value: Any, path: str) -> dict:
    artifact = _object(value, path, _ARTIFACT_FIELDS)
    _identifier(artifact["artifact_id"], path + ".artifact_id")
    _storage_key(artifact["storage_key"], path + ".storage_key")
    _hash(artifact["sha256"], path + ".sha256")
    _string(artifact["media_type"], path + ".media_type")
    if type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0:
        _fail(path + ".size_bytes", "must be a non-negative integer")
    _enum(artifact["role"], {"source", "derived"}, path + ".role")
    return artifact


def _artifacts(value: Any, path: str) -> Dict[str, dict]:
    result = {}
    for index, item in enumerate(_array(value, path)):
        artifact = _artifact(item, "{}[{}]".format(path, index))
        artifact_id = artifact["artifact_id"]
        if artifact_id in result:
            _fail(path, "artifact identifiers must be unique")
        result[artifact_id] = artifact
    return result


def _context(payload: dict, path: str) -> None:
    if type(payload["schema_version"]) is not str or payload["schema_version"] != SCHEMA_VERSION:
        _fail(path + ".schema_version", "unsupported schema version")
    for field in _CONTEXT_FIELDS:
        _identifier(payload[field], path + "." + field)


def _validate_request(payload: Any) -> dict:
    request = _object(payload, "request", _REQUEST_FIELDS)
    _context(request, "request")
    _artifacts(request["input_artifacts"], "request.input_artifacts")
    _object(request["parameters"], "request.parameters")
    _json(request["parameters"], "request.parameters")
    if request["protocol_id"] is not None:
        _identifier(request["protocol_id"], "request.protocol_id")
    return request


def validate_request(payload: dict) -> dict:
    """Validate a module request and return an independent, unchanged copy."""
    return deepcopy(_validate_request(payload))


def _message(value: Any, path: str) -> None:
    entry = _object(value, path, {"code", "message"})
    _identifier(entry["code"], path + ".code")
    _string(entry["message"], path + ".message")


def _observation(value: Any, path: str, known_artifacts: Dict[str, dict]) -> dict:
    observation = _object(value, path, _OBSERVATION_FIELDS)
    _identifier(observation["observation_id"], path + ".observation_id")
    concept = observation["concept"]
    if type(concept) is not str or _CONCEPT_PATTERN.fullmatch(concept) is None:
        _fail(path + ".concept", "must be a qualified dot-separated concept")
    status = _enum(
        observation["assessment_status"], {"assessed", "not_assessed", "not_applicable"},
        path + ".assessment_status",
    )
    observed = observation["value"]
    if status == "assessed":
        if observed is None or type(observed) not in (bool, str, int, float):
            _fail(path + ".value", "assessed observations require a non-null JSON scalar")
        _json(observed, path + ".value")
    elif observed is not None:
        _fail(path + ".value", "unassessed and not-applicable observations require null")
    unit = observation["unit"]
    if unit is not None:
        _string(unit, path + ".unit")
    if type(observed) in (int, float) and unit is None:
        _fail(path + ".unit", "numeric measurements require a unit")
    if type(observed) in (bool, str) and unit is not None:
        _fail(path + ".unit", "boolean and string observations use a null unit")

    anatomy = _object(observation["anatomy"], path + ".anatomy", _ANATOMY_FIELDS)
    _string(anatomy["region"], path + ".anatomy.region")
    _enum(anatomy["side"], {"left", "right", "bilateral", "midline", "unspecified"}, path + ".anatomy.side")
    if anatomy["level"] is not None:
        _string(anatomy["level"], path + ".anatomy.level")
    _enum(anatomy["numbering_status"], {"confirmed", "needs_confirmation", "not_applicable"}, path + ".anatomy.numbering_status")
    _enum(observation["quality"], {"usable", "unreliable", "unknown"}, path + ".quality")
    origin = _enum(
        observation["origin"],
        {"patient_report", "clinician_entered", "automatically_measured", "model_predicted", "synthetic_demo"},
        path + ".origin",
    )
    _string(observation["method"], path + ".method")
    _acquisition_time(observation["acquired_at"], path + ".acquired_at")
    if observation["coordinate_frame"] is not None:
        _string(observation["coordinate_frame"], path + ".coordinate_frame")

    evidence_refs = _array(observation["evidence_refs"], path + ".evidence_refs")
    if status == "assessed" and not evidence_refs:
        _fail(path + ".evidence_refs", "assessed observations require source evidence")
    for index, item in enumerate(evidence_refs):
        evidence_path = "{}.evidence_refs[{}]".format(path, index)
        evidence = _object(item, evidence_path, {"artifact_id", "selector"})
        artifact_id = _identifier(evidence["artifact_id"], evidence_path + ".artifact_id")
        if artifact_id not in known_artifacts:
            _fail(evidence_path + ".artifact_id", "must reference a declared artifact")
        _object(evidence["selector"], evidence_path + ".selector")
        _json(evidence["selector"], evidence_path + ".selector")

    model = observation["model"]
    if origin == "model_predicted" and model is None:
        _fail(path + ".model", "model predictions require model provenance")
    if model is not None:
        model = _object(model, path + ".model", _MODEL_FIELDS)
        _identifier(model["id"], path + ".model.id")
        _identifier(model["version"], path + ".model.version")
        _hash(model["weights_sha256"], path + ".model.weights_sha256")
    return observation


def validate_result(payload: dict, request: Optional[dict] = None) -> dict:
    """Validate observations, lineage and optional request context; copy data.

    A standalone result must declare all referenced artifacts.  Supplying its
    validated request additionally permits input artifact references.  A shared
    artifact ID must have exactly identical metadata in input and output.
    """
    result = _object(payload, "result", _RESULT_FIELDS)
    _context(result, "result")
    _identifier(result["result_id"], "result.result_id")
    status = _enum(result["status"], {"complete", "partial", "failed"}, "result.status")
    artifacts = _artifacts(result["artifacts"], "result.artifacts")
    known_artifacts = dict(artifacts)
    if request is not None:
        validated_request = _validate_request(request)
        for field in _CONTEXT_FIELDS:
            if result[field] != validated_request[field]:
                _fail("result." + field, "must match request context")
        for artifact_id, artifact in _artifacts(validated_request["input_artifacts"], "request.input_artifacts").items():
            if artifact_id in artifacts and artifacts[artifact_id] != artifact:
                _fail("result.artifacts", "shared artifact identifiers require identical metadata")
            known_artifacts[artifact_id] = artifact

    observations = _array(result["observations"], "result.observations")
    seen_observations = set()
    for index, item in enumerate(observations):
        observation = _observation(item, "result.observations[{}]".format(index), known_artifacts)
        if observation["observation_id"] in seen_observations:
            _fail("result.observations", "observation identifiers must be unique")
        seen_observations.add(observation["observation_id"])
        if status == "complete" and observation["assessment_status"] == "not_assessed":
            _fail("result.observations", "complete results cannot contain unassessed observations")
    for index, warning in enumerate(_array(result["warnings"], "result.warnings")):
        _message(warning, "result.warnings[{}]".format(index))
    if status == "failed":
        if result["error"] is None:
            _fail("result.error", "failed results require an error")
        _message(result["error"], "result.error")
        if observations:
            _fail("result.observations", "failed results cannot contain observations")
    elif result["error"] is not None:
        _fail("result.error", "complete and partial results require null error")
    return deepcopy(result)
