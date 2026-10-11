#!/usr/bin/env python3
"""Run raw FLIR -> automatic generic ROI in a real ProcessProvider workspace."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from dorsopathy_modules.thermography_raw import RawThermographyProvider
from dorsopathy_platform.contracts import loads_json
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.storage import ArtifactStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jpeg", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Local study directory (real data must stay outside Git)")
    parser.add_argument("--exiftool", required=True, help="Trusted ExifTool executable or upstream source script")
    parser.add_argument("--perl", help="Trusted Perl interpreter for an upstream source script")
    parser.add_argument("--worker-python", default=sys.executable)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "artifacts")
    jpeg = store.add_file(args.jpeg, "image/jpeg")
    with args.manifest.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        parser.error("Acquisition manifest exceeds 64 KiB")
    manifest = loads_json(content)
    if manifest.get("jpeg_artifact_id") == "bind-on-import":
        manifest["jpeg_artifact_id"] = jpeg["artifact_id"]
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode()
    (args.output / "acquisition-bound.json").write_bytes(raw)
    manifest_ref = store.add_bytes(raw, "application/json")
    job_id = "job-thermal-" + uuid.uuid4().hex[:16]
    request = {"schema_version": "1.0", "job_id": job_id, "episode_id": "episode-local-thermal",
               "module_id": "thermography", "module_version": "0.2.0", "input_artifacts": [jpeg, manifest_ref],
               "parameters": {}, "protocol_id": manifest["protocol_id"]}
    root = Path(__file__).resolve().parents[1]
    # ProcessProvider changes cwd to a private workspace; imports require an
    # absolute source path, not a relative PYTHONPATH=src inherited by the CLI.
    os.environ["PYTHONPATH"] = str(root / "src") + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    command = [str(Path(args.worker_python).resolve()), "-m", "dorsopathy_workers.thermography", "--exiftool", str(Path(args.exiftool).resolve())]
    if args.perl:
        command += ["--perl", str(Path(args.perl).resolve())]
    provider = ProcessProvider(RawThermographyProvider.spec, tuple(command), args.output / "workers", timeout_seconds=120)
    started = time.monotonic()
    try:
        result = provider.analyze(request, ProviderContext(store))
    finally:
        provider.close()
    (args.output / "request.json").write_text(json.dumps(request, indent=2, allow_nan=False) + "\n")
    (args.output / "module-result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    for ref in result["artifacts"]:
        if ref["role"] != "derived":
            continue
        if ref["media_type"] == "application/json":
            value = loads_json(store.read(ref))
            kind = value.get("source_kind")
            filename = {"thermography.raw-measurements.v1": "measurements.json", "thermography.flir-radiometry.v1": "radiometry.json", "thermography.temperature-matrix.v1": "temperature-matrix.json"}.get(kind)
            if filename:
                (args.output / filename).write_text(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False) + "\n")
                if filename == "measurements.json":
                    for key, name in (("mask_artifact_id", "automatic-mask.png"), ("heatmap_artifact_id", "automatic-heatmap.png")):
                        image_ref = next(item for item in result["artifacts"] if item["artifact_id"] == value[key])
                        (args.output / name).write_bytes(store.read(image_ref))
        elif ref["media_type"] == "image/svg+xml":
            (args.output / "automatic-heatmap.svg").write_bytes(store.read(ref))
    run = {"status": result["status"], "elapsed_seconds": time.monotonic() - started,
           "raw_sha256": jpeg["sha256"], "origin": manifest["origin"], "source_provenance": manifest["source_provenance"],
           "method": "raw_flir_exiftool_planck_automatic_gross_thermal_foreground", "worker_command": command,
           "observations": [{key: obs[key] for key in ("concept", "assessment_status", "value", "unit", "quality")} for obs in result["observations"]]}
    (args.output / "run-metrics.json").write_text(json.dumps(run, indent=2, allow_nan=False) + "\n")
    print(json.dumps(run, indent=2, allow_nan=False))
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
