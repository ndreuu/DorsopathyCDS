#!/usr/bin/env python3
"""Run a raw ultrasound PNG/DICOM through the versioned isolated provider."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dorsopathy_modules.ultrasound_raw import RawUltrasoundProvider
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.storage import ArtifactStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--upstream-root", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    if bool(args.upstream_root) != bool(args.checkpoint):
        parser.error("upstream-root and checkpoint must be provided together")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("request.json", "result.json", "analysis.json", "source.png", "mask.png", "overlay.png"):
        (args.output_dir / name).unlink(missing_ok=True)
    store = ArtifactStore(args.output_dir / "artifacts")
    manifest = json.loads(args.manifest.read_text())
    media_type = "image/png" if args.frame.suffix.lower() == ".png" else "application/dicom"
    frame = store.add_file(args.frame, media_type)
    manifest["frame_artifact_id"] = frame["artifact_id"]
    source = store.add_bytes(json.dumps(manifest, allow_nan=False).encode(), "application/json")
    request = {"schema_version": "1.0", "job_id": "raw-ultrasound-local", "episode_id": "raw-ultrasound-independent",
        "module_id": "ultrasound", "module_version": "0.2.0", "input_artifacts": [source, frame],
        "parameters": {}, "protocol_id": manifest["protocol_id"]}
    command = [str(args.worker_python), "-c", "import sys; sys.path.insert(0, " + repr(str(ROOT / "src")) + "); from dorsopathy_workers.ultrasound.worker import main; raise SystemExit(main())"]
    if args.upstream_root:
        command += ["--upstream-root", str(args.upstream_root.resolve()), "--checkpoint", str(args.checkpoint.resolve()), "--device", args.device]
    provider = ProcessProvider(RawUltrasoundProvider.spec, tuple(command), args.output_dir / "worker", args.timeout_seconds)
    try:
        result = provider.analyze(request, ProviderContext(store))
    finally:
        provider.close()
    (args.output_dir / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    (args.output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for ref in result["artifacts"]:
        if ref["role"] != "derived" or ref["media_type"] != "application/json":
            continue
        with store.open(ref) as stream:
            summary = json.load(stream)
        if summary.get("schema_version") != "ultrasound.raw.analysis.v1":
            continue
        (args.output_dir / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        images = {"source.png": summary["source_png_artifact_id"],
            "mask.png": summary["segmentation"].get("mask_artifact_id"),
            "overlay.png": summary["segmentation"].get("overlay_artifact_id")}
        refs = {item["artifact_id"]: item for item in result["artifacts"]}
        for name, identity in images.items():
            if identity:
                with store.open(refs[identity]) as stream, (args.output_dir / name).open("wb") as out:
                    shutil.copyfileobj(stream, out)
    print(json.dumps({"status": result["status"], "result": str(args.output_dir / "result.json"),
        "observations": [{"concept": o["concept"], "value": o["value"], "unit": o["unit"], "assessment_status": o["assessment_status"]} for o in result["observations"]]}, ensure_ascii=False))
    return 0 if result["status"] in {"complete", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
