#!/usr/bin/env python3
"""Run fresh MRI pathology extraction through the real persisted platform flow.

The selected input must be a recorded, adult, single sagittal T2 MHA/NIfTI.
This is a research example; no complaints or examination facts are invented.
"""
import argparse
import json
from pathlib import Path
import uuid

from dorsopathy_platform.api_contracts import default_manifest
from dorsopathy_platform.bootstrap import create_application
from dorsopathy_platform.workflow import wait_for_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--store", type=Path, default=Path("var/mri-pathology-demo"))
    parser.add_argument("--output", type=Path, default=Path("var/mri-pathology-output"))
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    if args.input.is_symlink() or not args.input.is_file():
        parser.error("input must be a regular recorded MRI file")
    app = create_application(args.store, pathology_config=args.config)
    try:
        episode = app.create_episode()
        with args.input.open("rb") as stream:
            source = app.upload(episode["episode_id"], stream, "application/octet-stream", args.input.stat().st_size)
        manifest = default_manifest(episode["episode_id"], [{
            "module_id": "mri.pathology", "module_version": "0.1.0",
            "input_artifact_ids": [source["artifact_id"]], "parameters": {"processing_mode": "live"},
        }], mode="live", linkage="unlinked")
        run = app.start_run(manifest, "pathology-cli-" + uuid.uuid4().hex)
        print("Fresh inference started: " + run["run_id"], flush=True)
        run = wait_for_run(app, run["run_id"], timeout_seconds=args.timeout)
        snapshot = app.get_snapshot(run["snapshot_id"])
        result = snapshot["results"][0]
        report = app.get_report(run["run_id"])
        args.output.mkdir(parents=True, exist_ok=True)
        for name, data in (("run.json", run), ("result.json", result), ("report.json", report)):
            (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"run_id": run["run_id"], "episode_id": episode["episode_id"],
                          "status": result["status"], "source_sha256": source["sha256"],
                          "observations": [{"level": obs["anatomy"]["level"], "concept": obs["concept"],
                              "status": obs["assessment_status"], "value": obs["value"]} for obs in result["observations"]],
                          "output": str(args.output.resolve())}, ensure_ascii=False, indent=2), flush=True)
        return 2 if result["status"] == "failed" else 0
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
