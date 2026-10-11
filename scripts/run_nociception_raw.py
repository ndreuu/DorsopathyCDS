#!/usr/bin/env python3
"""Run a declared PMD-200 summary CSV import in a real isolated worker."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from dorsopathy_modules.nociception_raw import RawNociceptionProvider
from dorsopathy_platform.contracts import ContractError, loads_json
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.storage import ArtifactStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-python", default=sys.executable)
    args = parser.parse_args(argv)
    with args.manifest.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        parser.error("Acquisition manifest exceeds 64 KiB")
    manifest = loads_json(content)
    if type(manifest) is not dict:
        raise ContractError("Acquisition manifest must be an object")
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "artifacts")
    csv_ref = store.add_file(args.csv, "text/csv")
    if manifest.get("csv_artifact_id") == "bind-on-import":
        manifest["csv_artifact_id"] = csv_ref["artifact_id"]
    bound = json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode()
    (args.output / "acquisition-bound.json").write_bytes(bound)
    manifest_ref = store.add_bytes(bound, "application/json")
    request = {"schema_version": "1.0", "job_id": "job-noci-" + uuid.uuid4().hex[:16], "episode_id": "episode-local-nociception",
               "module_id": "nociception", "module_version": "0.2.0", "input_artifacts": [csv_ref, manifest_ref],
               "parameters": {}, "protocol_id": manifest["protocol_id"]}
    source_root = Path(__file__).resolve().parents[1] / "src"
    os.environ["PYTHONPATH"] = str(source_root) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    command = (str(Path(args.worker_python).resolve()), "-m", "dorsopathy_workers.nociception")
    provider = ProcessProvider(RawNociceptionProvider.spec, command, args.output / "workers", timeout_seconds=120)
    started = time.monotonic()
    try:
        result = provider.analyze(request, ProviderContext(store))
    finally:
        provider.close()
    (args.output / "request.json").write_text(json.dumps(request, indent=2, allow_nan=False) + "\n")
    (args.output / "module-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    for ref in result["artifacts"]:
        if ref["role"] != "derived":
            continue
        if ref["media_type"] == "application/json":
            value = loads_json(store.read(ref))
            filename = {"nociception.raw-series.v2": "imported-series.json", "nociception.raw-measurements.v2": "measurements.json"}.get(value.get("source_kind"))
            if filename:
                (args.output / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        elif ref["media_type"] in {"image/png", "image/svg+xml"}:
            (args.output / ("recorded-index.png" if ref["media_type"] == "image/png" else "recorded-index.svg")).write_bytes(store.read(ref))
    run = {"status": result["status"], "elapsed_seconds": time.monotonic() - started,
           "source_csv_sha256": csv_ref["sha256"], "origin": manifest["origin"], "source_provenance": manifest["source_provenance"],
           "vendor_serialization_verified_on_real_export": False, "worker_command": list(command),
           "observations": [{key: obs[key] for key in ("concept", "assessment_status", "value", "unit", "quality", "origin")} for obs in result["observations"]]}
    (args.output / "run-metrics.json").write_text(json.dumps(run, indent=2, allow_nan=False) + "\n")
    print(json.dumps(run, indent=2, allow_nan=False))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
