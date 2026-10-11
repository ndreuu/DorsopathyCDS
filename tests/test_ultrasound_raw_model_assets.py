"""Weights-only pickle bridge roundtrip and rejection; no model accuracy claim."""
import collections
import hashlib
import importlib.util
import io
import pickle
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dorsopathy_workers.ultrasound.fetch import MetadataReader, ModelPickler, StorageMetadata, TensorMetadata, model_archive, state_digest, validate_extracted_metadata


class UltrasoundAssetMetadataTests(unittest.TestCase):
    def test_untrusted_pickle_global_is_rejected(self):
        with self.assertRaises(ValueError): MetadataReader(io.BytesIO(b"cos\nsystem\n.")).load()

    @unittest.skipUnless(importlib.util.find_spec("torch"), "optional torch unavailable")
    def test_model_pickle_preserves_actual_storage_values_shape_stride(self):
        import torch
        storage = StorageMetadata(("storage", "FloatStorage", "0", "cuda:0", 4))
        tensor = TensorMetadata(storage, 0, (2, 2), (2, 1), False, collections.OrderedDict())
        payload = io.BytesIO(); ModelPickler(payload, protocol=2).dump({"model": {"reference": tensor}})
        actual_bytes = struct.pack("<4f", 1.5, -2, 7.25, 0)
        with tempfile.TemporaryDirectory() as directory:
            for sparse_large_offset in (False, True):
                path = Path(directory) / ("large.pt" if sparse_large_offset else "test.pt")
                with model_archive(path) as archive:
                    archive.writestr("image/data.pkl", payload.getvalue())
                    if sparse_large_offset:
                        # Sparse padding reproduces actual >2GiB record
                        # offsets without allocating/downloading large data.
                        archive.fp.seek((1 << 31) + 128)
                        archive.start_dir = archive.fp.tell()
                    archive.writestr("image/data/0", actual_bytes)
                    archive.writestr("image/byteorder", "little")
                    archive.writestr("image/version", "3\n")
                state = torch.load(path, map_location="cpu", weights_only=True, mmap=True)["model"]
                self.assertTrue(torch.equal(state["reference"], torch.tensor([[1.5, -2], [7.25, 0]])))
            self.assertEqual(zipfile.ZIP64_LIMIT, (1 << 31) - 1)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "optional torch unavailable")
    def test_same_shape_storage_swap_or_changed_digest_is_rejected(self):
        # Synthetic metadata validates identity checks, not model accuracy.
        model = {k: TensorMetadata(StorageMetadata(("storage", "FloatStorage", str(i), "cpu", 4)),
            0, (2, 2), (2, 1), False, collections.OrderedDict()) for i, k in enumerate(("first", "second"))}
        def encode(m):
            out = io.BytesIO(); ModelPickler(out, protocol=2).dump({"model": m}); return out.getvalue()
        original = encode(model)
        tensors = {str(i): {"sha256": hashlib.sha256(bytes([i]) * 16).hexdigest()} for i in range(2)}
        digest = state_digest(model, tensors)
        swapped = {k: TensorMetadata(v.storage, v.offset, v.shape, v.stride, v.requires_grad, v.hooks)
            for k, v in model.items()}
        swapped["first"].storage, swapped["second"].storage = swapped["second"].storage, swapped["first"].storage
        with patch("dorsopathy_workers.ultrasound.fetch.PICKLE_SHA", hashlib.sha256(original).hexdigest()):
            validate_extracted_metadata(original, original, tensors, digest)
            with self.assertRaisesRegex(ValueError, "mapping differs"):
                validate_extracted_metadata(original, encode(swapped), tensors, digest)
            with self.assertRaisesRegex(ValueError, "digest differs"):
                validate_extracted_metadata(original, original, tensors, "0" * 64)


if __name__ == "__main__": unittest.main()
