"""Actual released UltraSAM3 image weights and native text-prompt inference."""
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tarfile
import time
import textwrap
import types
import zipfile

from dorsopathy_platform.contracts import ContractError

UPSTREAM_COMMIT = "7ab334166fee7f87cf47a213d97731ad482dad3f"
MODEL_REVISION = "6cdd74d0c741c411b09d2d7c7b4703f003837c25"
WEIGHTS_SHA256 = "b57a47ecca055472191a4460aba1adb7f1d29526b375efae67c98e463225b63d"
WEIGHTS_SIZE = 10081250438
SOURCE_ARCHIVE_SHA256 = "5bbcac3e173fac7443fe5e840832fe404795cf042880c437ed9336e62011f015"


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _disabled_unused_dependency(*args, **kwargs):
    raise ContractError("Unsupported video/tracking-only dependency was invoked during image inference")


def unused_dependency_bridge():
    """Guard import-only tracking dependencies; never return a fake tensor.

    The released builder imports tracking and training dataset modules even with
    enable_inst_interactivity=False. Both guarded functions fail if called.
    """
    guards = []
    if importlib.util.find_spec("triton") is None:
        module = types.ModuleType("sam3.model.edt")
        module.edt_triton = _disabled_unused_dependency
        sys.modules["sam3.model.edt"] = module
        guards.append("unused_tracking_edt_import_guard_raises_if_called")
    if importlib.util.find_spec("decord") is None:
        module = types.ModuleType("decord")
        module.cpu = _disabled_unused_dependency
        module.VideoReader = _disabled_unused_dependency
        sys.modules["decord"] = module
        guards.append("unused_training_video_reader_import_guard_raises_if_called")
    return guards


class UltraSAM3Segmenter:
    def __init__(self, upstream_root, checkpoint, device="cpu", threads=2):
        self.upstream_root = Path(upstream_root).resolve()
        self.checkpoint = Path(checkpoint).resolve()
        self.device = device
        self.threads = threads
        self.processor = None
        self.model_ref = None
        self.provenance = None

    def _load(self):
        import torch
        code = self.upstream_root / "code"
        if not (code / "sam3/model_builder.py").is_file() or not (code / "LICENSE").is_file():
            raise ContractError("Pinned UltraSAM3 source root is unavailable")
        if not self.checkpoint.is_file():
            raise ContractError("Released UltraSAM3 checkpoint is unavailable")
        extracted = self.checkpoint.name == "UltraSAM3-image.pt"
        weights_hash = _hash(self.checkpoint)
        image_manifest = None
        if extracted:
            from .fetch import PICKLE_SHA, TAIL_SHA, MODEL_BYTES, MODEL_KEYS, index_release, validate_extracted_metadata
            image_manifest_path = self.checkpoint.parent / "image-weights.json"
            if not image_manifest_path.is_file():
                raise ContractError("Extracted UltraSAM3 weights require verified extraction provenance")
            image_manifest = json.loads(image_manifest_path.read_text())
            if (image_manifest.get("schema_version") != "ultrasam3.image-weights.v1"
                or image_manifest.get("checkpoint_revision") != MODEL_REVISION
                or image_manifest.get("upstream_commit") != UPSTREAM_COMMIT
                or image_manifest.get("source_pickle_sha256") != PICKLE_SHA
                or image_manifest.get("source_zip_metadata_sha256") != TAIL_SHA
                or image_manifest.get("model_tensor_bytes") != MODEL_BYTES
                or image_manifest.get("model_key_count") != MODEL_KEYS
                or image_manifest.get("image_checkpoint_sha256") != weights_hash
                or image_manifest.get("image_checkpoint_size_bytes") != self.checkpoint.stat().st_size
                or image_manifest.get("tensor_bytes_modified") is not False):
                raise ContractError("Extracted UltraSAM3 checkpoint provenance is invalid")
            entries = {e.filename: e for e in index_release(self.checkpoint.parent)}
            # Check every extracted storage against the independently pinned
            # original ZIP metadata and the extraction's actual SHA256 hashes.
            with zipfile.ZipFile(self.checkpoint) as archive:
                actual_tensors = {}
                for identity, item in image_manifest["tensor_storages"].items():
                    original = entries["archive/data/" + identity]
                    digest = hashlib.sha256(); size = 0
                    with archive.open("image/data/" + identity) as stream:
                        for b in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                            digest.update(b); size += len(b)
                    current = archive.getinfo("image/data/" + identity)
                    if size != original.file_size or current.CRC != original.CRC or digest.hexdigest() != item["sha256"]:
                        raise ContractError("Extracted UltraSAM3 storage differs from the released tensor")
                    actual_tensors[identity] = {"sha256": digest.hexdigest()}
                metadata_path = self.checkpoint.parent / "released-model-metadata.pkl"
                if not metadata_path.is_file() or metadata_path.stat().st_size > 1024 * 1024 or archive.getinfo("image/data.pkl").file_size > 1024 * 1024:
                    raise ContractError("Extracted UltraSAM3 requires bounded pinned tensor-mapping metadata")
                try:
                    validate_extracted_metadata(metadata_path.read_bytes(), archive.read("image/data.pkl"),
                        actual_tensors, image_manifest["model_state_sha256"])
                except ValueError as error:
                    raise ContractError(str(error)) from error
            if set(image_manifest["tensor_storages"]) != {str(i) for i in range(MODEL_KEYS)}:
                raise ContractError("Extracted UltraSAM3 native model storage set is incomplete")
        elif self.checkpoint.stat().st_size != WEIGHTS_SIZE or weights_hash != WEIGHTS_SHA256:
            raise ContractError("Released UltraSAM3 checkpoint size or SHA256 mismatch")
        asset_manifest = self.upstream_root.parent / "assets.json"
        if not asset_manifest.is_file():
            raise ContractError("UltraSAM3 requires its verified source asset manifest")
        manifest = json.loads(asset_manifest.read_text())
        if manifest.get("upstream_commit") != UPSTREAM_COMMIT or manifest.get("source_archive_sha256") != SOURCE_ARCHIVE_SHA256 or type(manifest.get("source_files")) is not dict:
            raise ContractError("UltraSAM3 source asset manifest is invalid")
        # Include every imported Python file and tokenizer; downloaded source
        # stays untouched. Hashes are independently compared during setup.
        files = {str(p.relative_to(self.upstream_root)): p for p in code.rglob("*.py")}
        files["code/LICENSE"] = code / "LICENSE"
        files["code/assets/bpe_simple_vocab_16e6.txt.gz"] = code / "assets/bpe_simple_vocab_16e6.txt.gz"
        if set(files) != set(manifest["source_files"]) or any(_hash(path) != manifest["source_files"][name] for name, path in files.items()):
            raise ContractError("UltraSAM3 source files differ from pinned assets")
        source_archive = self.upstream_root.parent / "source.tar.gz"
        if not source_archive.is_file() or _hash(source_archive) != SOURCE_ARCHIVE_SHA256:
            raise ContractError("Pinned UltraSAM3 source archive is unavailable or altered")
        with tarfile.open(source_archive) as archive:
            for relative, path in files.items():
                with archive.extractfile(self.upstream_root.name + "/" + relative) as stream:
                    expected = hashlib.sha256(stream.read()).hexdigest()
                if expected != manifest["source_files"][relative]:
                    raise ContractError("UltraSAM3 source asset hashes differ from the pinned official archive")
        torch.set_num_threads(self.threads)
        torch.set_num_interop_threads(1)
        sys.path.insert(0, str(code))
        guards = unused_dependency_bridge()
        import sam3.model_builder as builder
        from sam3.model.sam3_image_processor import Sam3Processor
        import sam3
        if Path(sam3.__file__).resolve().parent != code / "sam3":
            raise ContractError("UltraSAM3 imported an unpinned SAM3 package")
        # The released training checkpoint includes optimiser tensors. Mmap
        # avoids eagerly allocating its full 10GB in addition to the model.
        checkpoint = torch.load(str(self.checkpoint), map_location="cpu", weights_only=True, mmap=True)
        state = checkpoint["model"] if "model" in checkpoint else checkpoint
        if any("detector." in k for k in state):
            state = {k.replace("detector.", ""): v for k, v in state.items() if "detector." in k}
        original_position_encoding = builder._create_position_encoding
        if self.device != "cuda":
            # Only omit an optional CUDA cache warmup. PositionEmbeddingSine
            # computes the unchanged encodings lazily from input.device.
            def lazy_position_encoding(precompute_resolution=None):
                return original_position_encoding(precompute_resolution=None)
            builder._create_position_encoding = lazy_position_encoding
            guards.append("position_encoding_omit_cuda_cache_warmup_same_lazy_math")
            original_coords = builder.TransformerDecoder._get_coords
            def device_coords(height, width, device):
                return original_coords(height, width, "cpu" if str(device) == "cuda" else device)
            builder.TransformerDecoder._get_coords = staticmethod(device_coords)
            guards.append("decoder_coordinate_cache_cuda_allocation_to_cpu_same_math")
        if self.device == "cpu":
            from sam3.model.geometry_encoders import SequenceGeometryEncoder
            original_method = SequenceGeometryEncoder._encode_boxes
            method_source = textwrap.dedent(inspect.getsource(original_method))
            old = "scale = scale.pin_memory().to(device=boxes_xyxy.device, non_blocking=True)"
            new = "scale = scale.to(device=boxes_xyxy.device, non_blocking=True)"
            if method_source.count(old) != 1:
                raise ContractError("Pinned geometry encoder CPU bridge does not match the released method")
            scope = {}
            exec(compile(method_source.replace(old, new), "<ultrasound_cpu_geometry_scale_bridge>", "exec"),
                original_method.__globals__, scope)
            SequenceGeometryEncoder._encode_boxes = scope["_encode_boxes"]
            guards.append("geometry_scale_omit_cpu_pin_memory_same_tensor_values_and_math")
        network = builder.build_sam3_image_model(bpe_path=str(code / "assets/bpe_simple_vocab_16e6.txt.gz"),
            checkpoint_path=None, device="cpu", load_from_HF=False,
            eval_mode=True, enable_inst_interactivity=False, compile=False)
        missing, unexpected = network.load_state_dict(state, strict=False)
        if missing or unexpected:
            raise ContractError("Released UltraSAM3 checkpoint does not completely match the native image model")
        del state, checkpoint
        network = network.to(self.device)
        self.processor = Sam3Processor(network, device=self.device, confidence_threshold=.5)
        self.model_ref = {"id": "ultrasam3", "version": UPSTREAM_COMMIT[:12], "weights_sha256": weights_hash}
        self.provenance = {"upstream_commit": UPSTREAM_COMMIT, "checkpoint_revision": MODEL_REVISION,
            "checkpoint_size_bytes": self.checkpoint.stat().st_size, "checkpoint_sha256": weights_hash,
            "full_upstream_checkpoint_sha256_expected": WEIGHTS_SHA256,
            "full_upstream_checkpoint_sha256_verified": not extracted,
            "image_weight_extraction": image_manifest,
            "model_url": "https://huggingface.co/zhuqh19/UltraSAM3/tree/" + MODEL_REVISION,
            "code_license_url": "https://github.com/zhuqh19/UltraSAM3/blob/" + UPSTREAM_COMMIT + "/code/LICENSE",
            "license_note": "HF card declares Apache-2.0; bundled SAM3 code uses SAM License 2025-11-19; outer inference wrapper has no licence. No model/code redistribution.",
            "native_api": "build_sam3_image_model; Sam3Processor.set_image; Sam3Processor.set_text_prompt",
            "checkpoint_loading": "torch.load_weights_only_mmap; verify_pinned_key_storage_shape_stride_mapping_and_actual_state_digest; native_detector_prefix_selection; reject_missing_or_unexpected_keys",
            "source_code_modified": False, "compatibility_bridges": guards,
            "bridge_sha256": _hash(Path(__file__)), "torch": torch.__version__, "device": self.device,
            "resolution": 1008, "mask_threshold": .5, "score_threshold": .5,
            "geometric_prompts": False, "existing_predictions_reused": False,
            "scores_are_uncalibrated": True}

    def __call__(self, decoded, prompt):
        if self.processor is None:
            self._load()
        import numpy as np
        import torch
        from PIL import Image
        image = Image.fromarray(np.stack([np.frombuffer(bytes(row), dtype=np.uint8).reshape(decoded["width"], 3) for row in decoded["rows"]]))
        start = time.monotonic()
        with torch.inference_mode():
            state = self.processor.set_image(image)
            state = self.processor.set_text_prompt(prompt=prompt, state=state)
        masks, scores = state["masks"], state["scores"]
        if not torch.isfinite(scores).all():
            raise ContractError("UltraSAM3 returned nonfinite scores")
        details = {"model": self.model_ref, "prompt": prompt, "provenance": self.provenance,
            "elapsed_seconds": time.monotonic() - start, "candidate_count": int(len(scores)),
            "candidate_scores": scores.detach().cpu().tolist(), "status": "predicted"}
        if not len(scores):
            return {**details, "status": "unassessed", "reason": "no_predicted_mask", "mask": None}
        index = int(scores.argmax())
        mask = masks[index].detach().cpu().squeeze().numpy()
        if mask.shape != (decoded["height"], decoded["width"]):
            raise ContractError("UltraSAM3 mask was not restored to original image dimensions")
        return {**details, "score": float(scores[index]), "selection": "highest_model_score",
            "mask": mask.astype(bool).tolist()}
