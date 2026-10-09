"""Replay small synthetic report fixtures with exact recorded artifact hashes."""
import argparse
import json
from pathlib import Path
import tempfile

from dorsopathy_cds.episode_report import evaluate_snapshot
from dorsopathy_platform.storage import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("complete", "partial"), default="complete")
    options = parser.parse_args()
    folder = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix="dorsopathy-report-example-") as runtime:
        store = ArtifactStore(Path(runtime))
        for entry in json.loads((folder / "artifact_files.json").read_text(encoding="utf-8")):
            expected = entry["artifact"]
            actual = store.add_file(folder / entry["filename"], expected["media_type"], expected["role"])
            if actual != expected:
                raise ValueError("Synthetic fixture artifact integrity mismatch")
        snapshot = json.loads((folder / (options.case + "_snapshot.json")).read_text(encoding="utf-8"))
        manifest = json.loads((folder / (options.case + "_manifest.json")).read_text(encoding="utf-8"))
        report = evaluate_snapshot(snapshot, manifest, store)
        print(json.dumps({"report_id": report["report_id"], "snapshot_id": report["snapshot_id"],
                          "mode": "synthetic", "clinical_status": report["clinical_report"]["results"][0]["status"],
                          "warnings": report["warnings"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
