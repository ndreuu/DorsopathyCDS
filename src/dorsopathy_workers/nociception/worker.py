"""Versioned ModuleResult file exchange; no ANI/NOL algorithm or device driver."""
import argparse
from pathlib import Path

from dorsopathy_modules.nociception_raw import RawNociceptionProvider
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_io import read_worker_request, safe_exchange_path, write_worker_output
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore


class WorkerArtifacts:
    def __init__(self, exchange, envelope):
        self.store = ArtifactStore(Path(exchange) / "derived")
        self.inputs = {ref["artifact_id"]: ref for ref in envelope["request"]["input_artifacts"]}
        self.paths = {item["artifact_id"]: safe_exchange_path(exchange, item["relative_path"]) for item in envelope["inputs"]}

    def open(self, reference):
        if reference["artifact_id"] in self.inputs:
            if reference != self.inputs[reference["artifact_id"]]:
                raise ContractError("Nociception worker input identity mismatch")
            return self.paths[reference["artifact_id"]].open("rb")
        return self.store.open(reference)

    def read(self, reference):
        with self.open(reference) as stream:
            return stream.read()

    def add_file(self, path, media_type, role="source"):
        return self.store.add_file(path, media_type, "derived")

    def add_bytes(self, data, media_type, role="source"):
        return self.store.add_bytes(data, media_type, "derived")

    def descriptors(self, result):
        return [{"artifact_id": ref["artifact_id"], "relative_path": "derived/" + ref["storage_key"],
                 **{key: ref[key] for key in ("sha256", "size_bytes", "media_type", "role")}}
                for ref in result["artifacts"] if ref["artifact_id"] not in self.inputs]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Trusted PMD-200 documented-field CSV import")
    parser.add_argument("--exchange-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    envelope = read_worker_request(args.exchange_dir)
    request = envelope["request"]
    artifacts = WorkerArtifacts(args.exchange_dir, envelope)
    try:
        result = RawNociceptionProvider().analyze(request, ProviderContext(artifacts))
    except (ContractError, OSError, ValueError, OverflowError):
        result = result_envelope(request, [], request["input_artifacts"], "failed")
        result["error"] = {"code": "vendor_nociception_import_failed", "message": "Vendor CSV or acquisition metadata could not be validated; no index was assessed."}
        result = validate_result(result, request)
    write_worker_output(args.exchange_dir, result, artifacts.descriptors(result))
    return 0
