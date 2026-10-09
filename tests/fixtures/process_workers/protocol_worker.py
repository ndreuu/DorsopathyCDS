"""Synthetic subprocess double. Never registered by application bootstrap."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from dorsopathy_platform.process_io import read_worker_request, write_worker_output, MAX_JSON_BYTES
from dorsopathy_platform.results import observation_id_for, result_envelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange-dir", required=True)
    parser.add_argument("--mode", default="success")
    parser.add_argument("--pid-file")
    args = parser.parse_args()
    root = Path(args.exchange_dir)
    envelope = read_worker_request(root)
    request = envelope["request"]
    if args.mode in {"timeout", "child_exit"}:
        child = subprocess.Popen([sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(90)"])
        if args.pid_file:
            Path(args.pid_file).write_text(str(child.pid))
        if args.mode == "timeout":
            time.sleep(90)
    if args.mode == "fail":
        print("PRIVATE_PATIENT_TEXT", file=sys.stderr)
        return 7
    if args.mode == "malformed":
        (root / "output.json").write_bytes(b"PRIVATE_PATIENT_TEXT invalid-json")
        return 0
    if args.mode == "oversized":
        (root / "output.json").write_bytes(b" " * (MAX_JSON_BYTES + 1))
        return 0
    path = root / "derived.bin"
    path.write_bytes(b"synthetic process artifact")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    reference = {
        "artifact_id": "artifact-" + sha, "storage_key": "artifacts/" + sha,
        "sha256": sha, "size_bytes": path.stat().st_size,
        "media_type": "application/octet-stream", "role": "derived",
    }
    derived = [{**{field: reference[field] for field in reference if field != "storage_key"}, "relative_path": "derived.bin"}]
    evidence_id = request["input_artifacts"][0]["artifact_id"] if request["input_artifacts"] else reference["artifact_id"]
    observation = {
        "observation_id": observation_id_for(request["job_id"], "test.worker_input_present"),
        "concept": "test.worker_input_present", "assessment_status": "assessed",
        "value": True, "unit": None,
        "anatomy": {"region": "lower_back", "side": "unspecified", "level": None, "numbering_status": "not_applicable"},
        "quality": "usable", "origin": "synthetic_demo", "method": "synthetic subprocess fixture",
        "evidence_refs": [{"artifact_id": evidence_id, "selector": {"kind": "json", "pointer": "/synthetic"}}],
        "model": None, "acquired_at": None, "coordinate_frame": None,
    }
    result = result_envelope(request, [observation], request["input_artifacts"] + [reference])
    if args.mode == "stdout_only":
        print(json.dumps(result))
        return 0
    if args.mode == "wrong_context":
        result["job_id"] = "another-job"
    elif args.mode == "traversal":
        derived[0]["relative_path"] = "../derived.bin"
    elif args.mode == "symlink":
        (root / "linked.bin").symlink_to(path)
        derived[0]["relative_path"] = "linked.bin"
    elif args.mode == "corrupt":
        path.write_bytes(b"corrupt bytes")
    elif args.mode == "undeclared":
        derived = []
    elif args.mode == "reported_failure":
        result = result_envelope(request, [], request["input_artifacts"] + [reference], status="failed")
        result["error"] = {"code": "PRIVATE_PATIENT_CODE", "message": "PRIVATE_PATIENT_TEXT"}
        result["warnings"] = [{"code": "worker_detail", "message": "PRIVATE_PATIENT_WARNING"}]
    payload = {"schema_version": "1.0", "result": result, "derived": derived}
    if args.mode in {"success", "child_exit", "reported_failure"}:
        write_worker_output(root, result, derived)
    elif args.mode == "duplicate_json":
        encoded = json.dumps(payload)
        (root / "output.json").write_text(encoded[:-1] + ',"schema_version":"1.0"}')
    else:
        # Deliberately bypass helper to verify the adapter rejects hostile output.
        (root / "output.json").write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
