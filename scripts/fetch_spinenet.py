#!/usr/bin/env python3
"""Prepare the four pinned SpineNet assets; no ML imports or inference."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


UPSTREAM_COMMIT = "5e96ac54b1c1845a37d12e40bfd16f647d40acd8"
SCHEMA_VERSION = "spinenet-assets.v1"
LICENSE_URL = (
    "https://github.com/rwindsor1/SpineNet/blob/"
    + UPSTREAM_COMMIT
    + "/LICENCE.md"
)
WEIGHTS_BASE_URL = "https://www.robots.ox.ac.uk/~vgg/research/spinenet/weights/"
WEIGHT_FILES = {
    name: WEIGHTS_BASE_URL + name
    for name in (
        "grading/ckpt1.pt",
        "appearance/ckpt187.pt",
        "detect-vfr/ckpt435.pt",
        "context/ckpt16.pt",
    )
}
MAX_FILE_BYTES = 1024**3
MAX_MANIFEST_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024


def file_fingerprint(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular checkpoint: {path}")
    size = path.stat().st_size
    if not 0 < size <= MAX_FILE_BYTES:
        raise ValueError(f"Checkpoint size is outside the download budget: {path}")
    digest = hashlib.sha256()
    actual_size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            actual_size += len(chunk)
            if actual_size > MAX_FILE_BYTES:
                raise ValueError(f"Checkpoint exceeds the download budget: {path}")
            digest.update(chunk)
    if size != actual_size:
        raise ValueError(f"Checkpoint changed while it was being verified: {path}")
    return {"size_bytes": actual_size, "sha256": digest.hexdigest()}


def verify_checkout(upstream_root: Path) -> None:
    if not upstream_root.is_dir():
        raise ValueError(f"Missing pinned SpineNet checkout: {upstream_root}")
    try:
        result = subprocess.run(
            ["git", "-C", str(upstream_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Cannot verify the SpineNet checkout commit") from error
    if result.stdout.strip() != UPSTREAM_COMMIT:
        raise ValueError(f"SpineNet HEAD must be exactly {UPSTREAM_COMMIT}")
    if not (upstream_root / "spinenet" / "__init__.py").is_file():
        raise ValueError("The pinned checkout does not contain the SpineNet package")


def verify_layout(weights_root: Path, *, complete: bool = False) -> None:
    if weights_root.is_symlink():
        raise ValueError("The SpineNet weights directory cannot be a symlink")
    for relative in WEIGHT_FILES:
        expected = weights_root / relative
        directory = expected.parent
        if directory.is_symlink():
            raise ValueError(f"Model directory cannot be a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Model directory is not a directory: {directory}")
        if expected.is_symlink():
            raise ValueError(f"Checkpoint cannot be a symlink: {expected}")
        candidates = (
            [path for path in directory.iterdir() if path.suffix.lower() == ".pt"]
            if directory.exists()
            else []
        )
        if any(path.name != expected.name for path in candidates):
            raise ValueError(f"Unexpected .pt checkpoint would affect model selection: {directory}")
        if complete and (len(candidates) != 1 or not expected.is_file()):
            raise ValueError(f"Exactly one pinned .pt checkpoint is required: {directory}")


def load_manifest(path: Path) -> dict | None:
    if not path.exists():
        if path.is_symlink():
            raise ValueError("Manifest cannot be a symlink")
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Invalid SpineNet asset manifest file")
    try:
        with path.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Cannot read the SpineNet asset manifest") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("upstream_commit") != UPSTREAM_COMMIT
        or manifest.get("license_url") != LICENSE_URL
        or not isinstance(manifest.get("files"), dict)
        or set(manifest["files"]) != set(WEIGHT_FILES)
    ):
        raise ValueError("SpineNet asset manifest does not match the pinned source/assets")
    for relative, url in WEIGHT_FILES.items():
        entry = manifest["files"][relative]
        if (
            not isinstance(entry, dict)
            or set(entry) != {"url", "size_bytes", "sha256"}
            or entry.get("url") != url
            or type(entry.get("size_bytes")) is not int
            or not 0 < entry["size_bytes"] <= MAX_FILE_BYTES
            or not isinstance(entry.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
        ):
            raise ValueError(f"Invalid pinned checkpoint record: {relative}")
    return manifest


def matches_record(path: Path, record: dict | None) -> bool:
    if record is None or not path.exists():
        return False
    actual = file_fingerprint(path)
    return all(actual[key] == record[key] for key in ("size_bytes", "sha256"))


def validate_response(response, url: str) -> int | None:
    if response.status != 200:
        raise ValueError(f"Unexpected HTTP status for {url}: {response.status}")
    final_url = urllib.parse.urlsplit(response.geturl())
    if (
        final_url.scheme != "https"
        or final_url.hostname not in {"www.robots.ox.ac.uk", "robots.ox.ac.uk"}
        or final_url.username is not None
        or final_url.password is not None
        or final_url.port not in (None, 443)
    ):
        raise ValueError("Weight download redirected outside the official Oxford HTTPS server")
    content_type = response.headers.get_content_type().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        raise ValueError("The weight server returned an HTML page")
    length = response.headers.get("Content-Length")
    if length is None:
        return None
    try:
        length = int(length)
    except ValueError as error:
        raise ValueError("Invalid HTTP Content-Length") from error
    if not 0 < length <= MAX_FILE_BYTES:
        raise ValueError("HTTP Content-Length exceeds the checkpoint budget or is empty")
    return length


def download_once(url: str, target: Path, *, timeout: float, expected: dict | None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + target.name + ".", suffix=".part", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "DorsopathyCDS-SpineNet-assets/1", "Accept-Encoding": "identity"},
        )
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(file_descriptor, "wb") as output:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                declared_size = validate_response(response, url)
                while chunk := response.read(CHUNK_BYTES):
                    if size == 0:
                        prefix = chunk[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
                        if prefix.startswith((b"<!doctype html", b"<html", b"<?xml")):
                            raise ValueError("The weight server returned markup instead of a checkpoint")
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError("Downloaded checkpoint exceeds the 1 GiB budget")
                    output.write(chunk)
                    digest.update(chunk)
            if size == 0 or (declared_size is not None and size != declared_size):
                raise ValueError("Downloaded checkpoint does not match its HTTP size")
            output.flush()
            os.fsync(output.fileno())
        record = {"url": url, "size_bytes": size, "sha256": digest.hexdigest()}
        if expected is not None and record != expected:
            raise ValueError("Downloaded checkpoint differs from the existing pinned manifest")
        os.replace(temporary, target)
        return record
    finally:
        # A failed transfer never replaces a complete checkpoint.
        temporary.unlink(missing_ok=True)


def download(url: str, target: Path, *, timeout: float, retries: int, expected: dict | None) -> dict:
    for attempt in range(1, retries + 1):
        try:
            return download_once(url, target, timeout=timeout, expected=expected)
        except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError) as error:
            if attempt == retries:
                raise RuntimeError(f"Cannot download {target.name} after {retries} attempts: {error}") from error
            print(f"Retrying {target.name} after attempt {attempt}: {error}", file=sys.stderr, flush=True)
            time.sleep(min(2 ** (attempt - 1), 5))
    raise AssertionError("Download retry loop did not return")


def write_manifest(path: Path, manifest: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest.", suffix=".part", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(upstream_root: Path, *, timeout: float = 60, retries: int = 3, verify_only: bool = False) -> dict:
    verify_checkout(upstream_root)
    weights_root = upstream_root / "spinenet" / "weights"
    verify_layout(weights_root)
    manifest_path = weights_root / "manifest.json"
    previous = load_manifest(manifest_path)
    if verify_only and previous is None:
        raise ValueError("A local SpineNet asset manifest is required for verification")
    files = {}
    for relative, url in WEIGHT_FILES.items():
        target = weights_root / relative
        record = previous["files"][relative] if previous else None
        if matches_record(target, record):
            print(f"Verified {relative}", flush=True)
            files[relative] = record
            continue
        if verify_only:
            raise ValueError(f"Checkpoint does not match its pinned manifest: {relative}")
        print(f"Downloading {relative} from the official Oxford server", flush=True)
        files[relative] = download(url, target, timeout=timeout, retries=retries, expected=record)
    verify_layout(weights_root, complete=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "upstream_commit": UPSTREAM_COMMIT,
        "files": files,
        "license_url": LICENSE_URL,
    }
    if not verify_only:
        write_manifest(manifest_path, manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, default=Path("var/upstream/SpineNet"))
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--retries", type=int, default=3, help="Total attempts per checkpoint (1–8)")
    parser.add_argument("--verify-only", action="store_true", help="Rehash existing assets without network access")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 300 or not 1 <= args.retries <= 8:
        parser.error("timeout must be 1–300 seconds and retries must be 1–8")
    try:
        manifest = prepare(
            args.upstream_root.resolve(), timeout=args.timeout_seconds, retries=args.retries,
            verify_only=args.verify_only,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"SpineNet asset preparation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "verified", "upstream_commit": manifest["upstream_commit"],
        "checkpoint_count": len(manifest["files"]),
        "total_size_bytes": sum(entry["size_bytes"] for entry in manifest["files"].values()),
        "manifest": str(args.upstream_root / "spinenet" / "weights" / "manifest.json"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
