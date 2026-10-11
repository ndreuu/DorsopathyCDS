"""Isolated raw PNG/DICOM intake and optional actual UltraSAM3 inference."""
import argparse
import json
from pathlib import Path
import traceback

from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_io import read_worker_request, safe_exchange_path, write_worker_output
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_modules.ultrasound_raw import RawUltrasoundProvider
from .imaging import load_frame


class WorkerArtifacts:
    def __init__(self, exchange, envelope):
        self.exchange = Path(exchange)
        self.store = ArtifactStore(self.exchange / "derived")
        self.inputs = {ref["artifact_id"]: ref for ref in envelope["request"]["input_artifacts"]}
        self.paths = {item["artifact_id"]: safe_exchange_path(exchange, item["relative_path"]) for item in envelope["inputs"]}

    def open(self, ref):
        if ref["artifact_id"] in self.inputs:
            if ref != self.inputs[ref["artifact_id"]]:
                raise ContractError("Ultrasound worker source identity mismatch")
            return self.paths[ref["artifact_id"]].open("rb")
        return self.store.open(ref)

    def add_file(self, path, media_type, role="derived"):
        return self.store.add_file(path, media_type, "derived")

    def add_bytes(self, data, media_type, role="derived"):
        return self.store.add_bytes(data, media_type, "derived")

    def descriptors(self, result):
        return [{"artifact_id": ref["artifact_id"], "relative_path": "derived/" + ref["storage_key"],
            **{key: ref[key] for key in ("sha256", "size_bytes", "media_type", "role")}}
            for ref in result["artifacts"] if ref["artifact_id"] not in self.inputs]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange-dir", type=Path, required=True)
    parser.add_argument("--upstream-root", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if bool(args.upstream_root) != bool(args.checkpoint) or not 1 <= args.threads <= 8:
        parser.error("Provide upstream-root and checkpoint together; threads must be 1..8")
    envelope = read_worker_request(args.exchange_dir)
    request = envelope["request"]
    artifacts = WorkerArtifacts(args.exchange_dir, envelope)
    segmenter = None
    if args.upstream_root:
        from .model import UltraSAM3Segmenter
        actual_segmenter = UltraSAM3Segmenter(args.upstream_root, args.checkpoint, args.device, args.threads)
        def segmenter(decoded, prompt):
            try:
                return actual_segmenter(decoded, prompt)
            except Exception as error:
                traceback.print_exc()
                return {"mask": None, "model": actual_segmenter.model_ref,
                    "status": "unassessed", "reason": "model_inference_failed",
                    "error_type": type(error).__name__,
                    "detail": str(error) if isinstance(error, ContractError) else "model_runtime_failed",
                    "fresh_inference_successful": False}
    try:
        result = RawUltrasoundProvider(segmenter=segmenter, frame_loader=load_frame).analyze(request, ProviderContext(artifacts))
    except Exception as error:
        traceback.print_exc()
        # Failures retain evidence and unknown features, never a negative finding.
        audit = artifacts.add_bytes(json.dumps({"schema_version": "ultrasound.raw.failure.v1", "processing": "fresh",
            "status": "failed", "error_type": type(error).__name__,
            "reason": str(error) if isinstance(error, ContractError) else "decoder_or_model_runtime_failed",
            "fresh_inference_successful": False}, allow_nan=False).encode(), "application/json")
        result = result_envelope(request, [], [*request["input_artifacts"], audit], "failed")
        result["error"] = {"code": "ultrasound_raw_failed", "message": "Raw ultrasound import or model inference failed; no muscle finding was inferred."}
    result = validate_result(result, request)
    write_worker_output(args.exchange_dir, result, artifacts.descriptors(result))
    return 0
