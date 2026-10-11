#!/usr/bin/env python3
"""Run original depth PNG + camera metadata + acquisition manifest; no manual landmarks."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dorsopathy_modules.lidar_raw import RawLidarProvider
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--camera", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("var/lidar-raw-run"))
    parser.add_argument("--python-executable", type=Path)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "store")
    data = json.loads(args.manifest.read_text())
    depth = store.add_file(args.depth, "image/png")
    camera = store.add_file(args.camera, "application/json" if data["camera_format"] == "depth-camera-calibration.v1" else "application/xml")
    data["depth_artifact_id"] = depth["artifact_id"]
    data["camera_artifact_id"] = camera["artifact_id"]
    source = store.add_bytes(json.dumps(data, sort_keys=True, allow_nan=False).encode(), "application/json")
    request = {
        "schema_version": "1.0", "job_id": "raw-lidar-run", "episode_id": "raw-lidar-standalone",
        "module_id": "lidar.posture", "module_version": "0.2.0", "protocol_id": data["protocol_id"],
        "input_artifacts": [source, depth, camera], "parameters": {},
    }
    provider = RawLidarProvider(python_executable=args.python_executable, workspace_root=args.output / "workers")
    try:
        result = provider.analyze(request, ProviderContext(store))
    finally:
        provider.close()
    write_json(args.output / "source.json", data)
    write_json(args.output / "request.json", request)
    write_json(args.output / "result.json", result)
    derived = {}
    for reference in result["artifacts"]:
        if reference["role"] != "derived":
            continue
        if reference["media_type"] == "application/json":
            payload = json.loads(store.read(reference))
            name = "method.json" if payload.get("schema_version") == "lidar-depth-method.v2" else "measurements.json"
            write_json(args.output / name, payload)
        elif reference["media_type"] in {"image/png", "application/x-ply", "text/csv"}:
            name = {"image/png": "foreground.png", "application/x-ply": "cloud.ply", "text/csv": "component.csv"}[reference["media_type"]]
            with store.open(reference) as stream, (args.output / name).open("wb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(chunk)
        else:
            continue
        derived[name] = reference
    run = {
        "schema_version": "raw-lidar-run.v1", "input_depth_file": str(args.depth.resolve()),
        "input_camera_file": str(args.camera.resolve()), "manifest_template": str(args.manifest.resolve()),
        "depth_artifact": depth, "camera_artifact": camera, "manifest_artifact": source,
        "origin": data["origin"], "status": result["status"],
        "observations": len(result["observations"]),
        "assessed_observations": sum(item["assessment_status"] == "assessed" for item in result["observations"]),
        "derived": derived,
    }
    write_json(args.output / "run.json", run)
    print(json.dumps({key: run[key] for key in ("status", "observations", "assessed_observations", "origin")}))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
