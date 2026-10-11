#!/usr/bin/env python3
"""Run one independently sourced raw CSV + acquisition manifest, retaining artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dorsopathy_modules.emg_surface import RawEMGProvider
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("var/emg-raw-run"))
    parser.add_argument("--python-executable", type=Path)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "store")
    data = json.loads(args.manifest.read_text())
    raw = store.add_file(args.csv, "text/csv" if data["export"]["format"] == "csv" else "application/gzip")
    data["csv_artifact_id"] = raw["artifact_id"]
    source = store.add_bytes(json.dumps(data, allow_nan=False, sort_keys=True).encode(), "application/json")
    request = {
        "schema_version": "1.0", "job_id": "raw-emg-run", "episode_id": "raw-emg-standalone",
        "module_id": "emg.surface", "module_version": "0.2.0",
        "protocol_id": data["protocol_id"], "input_artifacts": [source, raw], "parameters": {},
    }
    provider = RawEMGProvider(python_executable=args.python_executable, workspace_root=args.output / "workers")
    try:
        result = provider.analyze(request, ProviderContext(store))
    finally:
        provider.close()
    write_json(args.output / "source.json", data)
    write_json(args.output / "request.json", request)
    write_json(args.output / "result.json", result)
    derived = {}
    for ref in result["artifacts"]:
        if ref["role"] != "derived":
            continue
        if ref["media_type"] == "application/json":
            payload = json.loads(store.read(ref))
            name = "method.json" if payload.get("schema_version") == "emg-signal-method.v2" else "measurements.json"
            write_json(args.output / name, payload)
        elif ref["media_type"] in {"image/png", "image/svg+xml", "text/csv"}:
            name = {"image/png": "signal.png", "image/svg+xml": "signal.svg", "text/csv": "processed.csv"}[ref["media_type"]]
            with store.open(ref) as stream, (args.output / name).open("wb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(chunk)
        else:
            continue
        derived[name] = ref
    run = {
        "schema_version": "raw-emg-run.v1", "input_file": str(args.csv.resolve()),
        "manifest_template": str(args.manifest.resolve()), "source_sha256": raw["sha256"],
        "source_size_bytes": raw["size_bytes"], "raw_artifact": raw,
        "manifest_artifact": source, "origin": data["origin"],
        "status": result["status"], "observations": len(result["observations"]),
        "assessed_observations": sum(item["assessment_status"] == "assessed" for item in result["observations"]),
        "derived": derived,
    }
    write_json(args.output / "run.json", run)
    print(json.dumps({key: run[key] for key in ("status", "observations", "assessed_observations", "origin")}))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
