"""Verified ModuleResult 1.0 exchange for bounded raw depth analysis."""
import argparse
from pathlib import Path

from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_io import read_worker_request, safe_exchange_path, write_worker_output
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore


class WorkerArtifacts:
    def __init__(self, exchange, envelope):
        self.exchange = Path(exchange)
        self.store = ArtifactStore(self.exchange / "derived")
        self.inputs = {ref["artifact_id"]: ref for ref in envelope["request"]["input_artifacts"]}
        self.paths = {item["artifact_id"]: safe_exchange_path(exchange, item["relative_path"]) for item in envelope["inputs"]}
    def open(self, ref):
        if ref["artifact_id"] in self.inputs:
            if ref != self.inputs[ref["artifact_id"]]:
                raise ContractError("Depth worker source identity mismatch")
            return self.paths[ref["artifact_id"]].open("rb")
        return self.store.open(ref)
    def read(self, ref):
        with self.open(ref) as stream:
            return stream.read()
    def add_file(self, path, media_type, role="derived"):
        return self.store.add_file(path, media_type, "derived")
    def add_bytes(self, data, media_type, role="derived"):
        return self.store.add_bytes(data, media_type, "derived")
    def descriptors(self, result):
        return [
            {"artifact_id": ref["artifact_id"], "relative_path": "derived/" + ref["storage_key"],
             **{key: ref[key] for key in ("sha256", "size_bytes", "media_type", "role")}}
            for ref in result["artifacts"] if ref["artifact_id"] not in self.inputs
        ]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    envelope = read_worker_request(args.exchange_dir)
    artifacts = WorkerArtifacts(args.exchange_dir, envelope)
    request = envelope["request"]
    try:
        from dorsopathy_modules.lidar_raw import RawLidarProvider
        result = RawLidarProvider().analyze(request, ProviderContext(artifacts))
    except ContractError:
        result = result_envelope(request, [], request["input_artifacts"], "failed")
        result["error"] = {"code": "raw_depth_input_invalid", "message": "Original depth/calibration or acquisition profile is unsupported."}
    write_worker_output(args.exchange_dir, result, artifacts.descriptors(result))
    return 0
