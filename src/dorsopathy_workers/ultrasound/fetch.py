"""Resume official UltraSAM3 assets; optionally extract real image weights only.

No optimiser tensors are fabricated. Image mode downloads the complete native
model tensor bytes, verifies ZIP entry CRCs and pins metadata from the official
release, then writes a model-only PyTorch archive and its own SHA256 manifest.
The full 10GB upstream SHA is claimed only in full mode after hashing all bytes.
"""
import argparse
import collections
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path
import pickle
import struct
import tarfile
import tempfile
import time
import urllib.request
import zipfile
import zlib

from .model import UPSTREAM_COMMIT, MODEL_REVISION, WEIGHTS_SHA256, WEIGHTS_SIZE, SOURCE_ARCHIVE_SHA256

URL = "https://huggingface.co/zhuqh19/UltraSAM3/resolve/" + MODEL_REVISION + "/UltraSAM3.pt?download=true"
CHUNK = 8 * 1024 * 1024
TAIL_SIZE = 1024 * 1024
TAIL_SHA = "2654c9f66ee8e76054dec5860c92e8e3ece97334956fb3fabf9e9a7f83162ca4"
PICKLE_SHA = "97bf1886a0114886ba458e12f405ae49793a816f83cc8dbe825fb62926ceb6b6"
MODEL_BYTES = 3371476184
MODEL_KEYS = 1134


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for b in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(b)
    return digest.hexdigest()


def fetch_range(start, end, timeout=120, attempts=4):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{WEIGHTS_SIZE}":
                    raise ValueError("Official checkpoint range response differs from request")
                data = response.read(end - start + 2)
            if len(data) != end - start + 1:
                raise ValueError("Official checkpoint range was truncated")
            return data
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(5, attempt + 1))


def fetch_source(root):
    archive = root / "source.tar.gz"
    if not archive.is_file() or sha_file(archive) != SOURCE_ARCHIVE_SHA256:
        url = "https://codeload.github.com/zhuqh19/UltraSAM3/tar.gz/" + UPSTREAM_COMMIT
        temporary = archive.with_suffix(".partial")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as stream:
            for b in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(b)
                if stream.tell() > 100 * 1024 * 1024:
                    raise ValueError("Source archive exceeds its bounded size")
        if sha_file(temporary) != SOURCE_ARCHIVE_SHA256:
            raise ValueError("Pinned source archive SHA256 mismatch")
        temporary.replace(archive)
    upstream = root / ("UltraSAM3-" + UPSTREAM_COMMIT)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for item in members:
            parts = Path(item.name).parts
            if item.name.startswith("/") or ".." in parts or not parts or parts[0] != upstream.name or item.issym() or item.islnk():
                raise ValueError("Source archive has unsafe paths")
        source.extractall(root, members=members)
    files = list((upstream / "code").rglob("*.py")) + [upstream / "code/LICENSE", upstream / "code/assets/bpe_simple_vocab_16e6.txt.gz"]
    manifest = {"schema_version": "ultrasam3.assets.v1", "upstream_commit": UPSTREAM_COMMIT,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_files": {str(p.relative_to(upstream)): sha_file(p) for p in files}}
    (root / "assets.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return upstream


def index_release(root):
    tail = root / "tail.zipdata"
    if not tail.is_file() or sha_file(tail) != TAIL_SHA:
        tail.write_bytes(fetch_range(WEIGHTS_SIZE - TAIL_SIZE, WEIGHTS_SIZE - 1))
    if sha_file(tail) != TAIL_SHA:
        raise ValueError("Pinned official ZIP metadata SHA256 mismatch")
    # Only ZIP metadata is represented in this temporary sparse file; it is
    # never passed to torch.load and never substitutes missing tensor bytes.
    with tempfile.TemporaryDirectory(dir=root) as directory:
        metadata_zip = Path(directory) / "metadata.zip"
        with metadata_zip.open("wb") as out:
            out.seek(WEIGHTS_SIZE - TAIL_SIZE); out.write(tail.read_bytes())
        with zipfile.ZipFile(metadata_zip) as archive:
            entries = archive.infolist()
    return entries


def download_prefix(root, byte_count, workers, timeout):
    parts = root / "checkpoint-parts"; parts.mkdir(exist_ok=True)
    count = (byte_count + CHUNK - 1) // CHUNK
    def download(index):
        lo, hi = index * CHUNK, min(byte_count, (index + 1) * CHUNK) - 1
        path = parts / str(index)
        # Existing complete official 8MiB ranges can also supply the final
        # partial chunk. Every used byte is rechecked by model-entry CRC later.
        if not path.is_file() or path.stat().st_size < hi - lo + 1:
            data = fetch_range(lo, hi, timeout=timeout)
            temporary = path.with_suffix(".partial"); temporary.write_bytes(data); temporary.replace(path)
        if path.stat().st_size > CHUNK:
            raise ValueError("Checkpoint range cache exceeds its declared span")
        return index, {"start": lo, "end": hi, "sha256": hashlib.sha256(path.read_bytes()[:hi - lo + 1]).hexdigest()}
    started = time.monotonic(); records = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, i) for i in range(count)]
        for completed, future in enumerate(as_completed(futures), 1):
            index, record = future.result(); records[str(index)] = record
            if completed % 8 == 0 or completed == count:
                print(json.dumps({"completed_ranges": completed, "total_ranges": count, "elapsed_seconds": round(time.monotonic() - started)}), flush=True)
    prefix = root / "model-prefix.bin"
    digest = hashlib.sha256()
    with prefix.open("wb") as out:
        for i in range(count):
            with (parts / str(i)).open("rb") as source:
                remaining = min(CHUNK, byte_count - i * CHUNK)
                while remaining:
                    b = source.read(min(1024 * 1024, remaining))
                    if not b:
                        raise ValueError("A checkpoint range cache entry is incomplete")
                    out.write(b); digest.update(b); remaining -= len(b)
    return prefix, records, digest.hexdigest()


class StorageMetadata:
    def __init__(self, reference):
        if type(reference) is not tuple or len(reference) != 5 or reference[0] != "storage" or reference[1] not in {"FloatStorage", "ComplexFloatStorage"}:
            raise ValueError("Unsupported checkpoint storage metadata")
        _, self.kind, self.key, _, self.count = reference


class TensorMetadata:
    def __init__(self, storage, offset, shape, stride, requires_grad, hooks, *extra):
        if extra or not isinstance(storage, StorageMetadata):
            raise ValueError("Unsupported checkpoint tensor metadata")
        self.storage, self.offset, self.shape, self.stride = storage, offset, shape, stride
        self.requires_grad, self.hooks = requires_grad, hooks

    def __reduce__(self):
        import torch
        return torch._utils._rebuild_tensor_v2, (self.storage, self.offset, self.shape, self.stride, self.requires_grad, self.hooks)


class MetadataReader(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"):
            return collections.OrderedDict
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return TensorMetadata
        if module == "torch" and name in {"FloatStorage", "ComplexFloatStorage"}:
            return name
        raise ValueError("Checkpoint pickle contains an unsupported global")

    def persistent_load(self, reference):
        return StorageMetadata(reference)


class ModelPickler(pickle.Pickler):
    def persistent_id(self, obj):
        if isinstance(obj, StorageMetadata):
            import torch
            return ("storage", getattr(torch, obj.kind), obj.key, "cpu", obj.count)
        return None


def tensor_mapping(model):
    """Semantic tensor identity; original accelerator location is omitted."""
    if type(model) not in (dict, collections.OrderedDict) or any(type(k) is not str or not isinstance(v, TensorMetadata) for k, v in model.items()):
        raise ValueError("Model metadata must contain named tensors only")
    return {k: (v.storage.kind, v.storage.key, v.storage.count, v.offset,
        tuple(v.shape), tuple(v.stride), v.requires_grad, tuple(v.hooks.items()))
        for k, v in model.items()}


def state_digest(model, tensors):
    digest = hashlib.sha256()
    for key, value in sorted(model.items()):
        identity = json.dumps({"key": key, "shape": value.shape, "stride": value.stride,
            "offset": value.offset, "kind": value.storage.kind,
            "storage_sha256": tensors[value.storage.key]["sha256"]},
            sort_keys=True, separators=(",", ":")).encode()
        digest.update(struct.pack("<I", len(identity))); digest.update(identity)
    return digest.hexdigest()


def validate_extracted_metadata(released_metadata, extracted_metadata, tensors, expected_digest):
    if hashlib.sha256(released_metadata).hexdigest() != PICKLE_SHA:
        raise ValueError("Released checkpoint metadata SHA256 differs")
    expected = MetadataReader(io.BytesIO(released_metadata)).load()["model"]
    extracted = MetadataReader(io.BytesIO(extracted_metadata)).load()
    if type(extracted) is not dict or set(extracted) != {"model"} or tensor_mapping(extracted["model"]) != tensor_mapping(expected):
        raise ValueError("Extracted tensor key/storage/shape/stride mapping differs from the release")
    if state_digest(expected, tensors) != expected_digest:
        raise ValueError("Extracted model-state digest differs from actual tensor bytes")


def _entry_offset(stream, entry):
    stream.seek(entry.header_offset); header = stream.read(30)
    signature, _, _, compression, _, _, _, _, _, name_len, extra_len = struct.unpack("<4s5H3I2H", header)
    name = stream.read(name_len).decode()
    if signature != b"PK\x03\x04" or name != entry.filename or compression != 0 or entry.file_size != entry.compress_size:
        raise ValueError("Official ZIP local header is invalid")
    return entry.header_offset + 30 + name_len + extra_len


@contextmanager
def model_archive(path):
    # Python conservatively emits ZIP64 offsets above 2GiB, although ZIP's
    # unsigned 32-bit offsets remain valid up to 4GiB. The PyTorch reader
    # rejects that mixed early-ZIP64 layout for this 3.37GB model. Use the
    # actual format limit; ZIP64 is still emitted whenever it is required.
    previous = zipfile.ZIP64_LIMIT
    zipfile.ZIP64_LIMIT = (1 << 32) - 1
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            yield archive
    finally:
        zipfile.ZIP64_LIMIT = previous


def extract_image_model(root, prefix, entries, ranges, prefix_sha):
    by_name = {e.filename: e for e in entries}
    with prefix.open("rb") as source:
        item = by_name["archive/data.pkl"]; offset = _entry_offset(source, item); source.seek(offset); metadata = source.read(item.file_size)
    if hashlib.sha256(metadata).hexdigest() != PICKLE_SHA or zlib.crc32(metadata) & 0xffffffff != item.CRC:
        raise ValueError("Pinned released checkpoint metadata differs")
    (root / "released-model-metadata.pkl").write_bytes(metadata)
    payload = MetadataReader(io.BytesIO(metadata)).load()
    model = payload["model"]
    if len(model) != MODEL_KEYS or any(not isinstance(v, TensorMetadata) for v in model.values()):
        raise ValueError("Released image model metadata is incomplete")
    storages = {v.storage.key: v.storage for v in model.values()}
    if set(storages) != {str(i) for i in range(MODEL_KEYS)} or sum(s.count * (8 if s.kind == "ComplexFloatStorage" else 4) for s in storages.values()) != MODEL_BYTES:
        raise ValueError("Released image model storage set differs")
    model_pickle = io.BytesIO(); ModelPickler(model_pickle, protocol=2).dump({"model": model})
    output = root / "UltraSAM3-image.pt"; temporary = output.with_suffix(".partial")
    tensors = {}
    with model_archive(temporary) as archive, prefix.open("rb") as source:
        archive.writestr("image/data.pkl", model_pickle.getvalue()); archive.writestr("image/byteorder", "little"); archive.writestr("image/version", "3\n")
        for identity in sorted(storages, key=int):
            item = by_name["archive/data/" + identity]; offset = _entry_offset(source, item); source.seek(offset)
            digest = hashlib.sha256(); crc = 0; remaining = item.file_size
            record = zipfile.ZipInfo("image/data/" + identity)
            record.file_size = item.file_size
            with archive.open(record, "w") as target:
                while remaining:
                    b = source.read(min(4 * 1024 * 1024, remaining))
                    if not b:
                        raise ValueError("Downloaded model prefix lacks actual tensor bytes")
                    target.write(b); digest.update(b); crc = zlib.crc32(b, crc); remaining -= len(b)
            if crc & 0xffffffff != item.CRC:
                raise ValueError("Released model tensor ZIP CRC mismatch")
            tensors[identity] = {"sha256": digest.hexdigest(), "size_bytes": item.file_size, "crc32": item.CRC}
    temporary.replace(output)
    manifest = {"schema_version": "ultrasam3.image-weights.v1", "upstream_commit": UPSTREAM_COMMIT,
        "checkpoint_revision": MODEL_REVISION, "source_url": URL, "upstream_checkpoint_size_bytes": WEIGHTS_SIZE,
        "upstream_full_checkpoint_sha256_expected": WEIGHTS_SHA256, "upstream_full_checkpoint_sha256_verified": False,
        "source_pickle_sha256": PICKLE_SHA, "source_zip_metadata_sha256": TAIL_SHA,
        "downloaded_prefix_sha256": prefix_sha, "ranges": ranges, "tensor_storages": tensors,
        "model_key_count": MODEL_KEYS, "model_tensor_bytes": MODEL_BYTES,
        "model_state_digest_method": "sha256_length_prefixed_sorted_key_shape_stride_offset_kind_storage_sha256_json_v1",
        "model_state_sha256": state_digest(model, tensors), "image_checkpoint_sha256": sha_file(output),
        "image_checkpoint_size_bytes": output.stat().st_size,
        "optimizer_tensors_included": False, "tensor_bytes_modified": False}
    (root / "image-weights.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("image", "full", "source"), default="image")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16 or not 10 <= args.timeout_seconds <= 600:
        parser.error("workers must be 1..16; timeout seconds must be 10..600")
    root = args.output_dir.resolve(); root.mkdir(parents=True, exist_ok=True)
    fetch_source(root)
    if args.mode == "source":
        return 0
    entries = index_release(root)
    if args.mode == "image":
        last = next(e for e in entries if e.filename == "archive/data/1133")
        # PyTorch ZIP padding can add a full extra alignment block. Derive
        # the exact endpoint from the actual local header, never a rounded
        # estimate that could silently omit final tensor bytes.
        header = fetch_range(last.header_offset, last.header_offset + 29,
            timeout=args.timeout_seconds)
        signature, _, _, compression, _, _, _, _, _, name_len, extra_len = struct.unpack("<4s5H3I2H", header)
        if signature != b"PK\x03\x04" or compression != 0 or name_len != len(last.filename.encode()):
            raise ValueError("Official model endpoint local header is invalid")
        end = last.header_offset + 30 + name_len + extra_len + last.file_size
    else:
        end = WEIGHTS_SIZE
    prefix, ranges, prefix_sha = download_prefix(root, end, args.workers, args.timeout_seconds)
    if args.mode == "full":
        if prefix_sha != WEIGHTS_SHA256:
            raise ValueError("Full released checkpoint SHA256 mismatch")
        prefix.replace(root / "UltraSAM3.pt")
        print(json.dumps({"full_checkpoint_sha256_verified": True, "sha256": prefix_sha}), flush=True)
    else:
        manifest = extract_image_model(root, prefix, entries, ranges, prefix_sha)
        print(json.dumps({k: manifest[k] for k in ("image_checkpoint_sha256", "model_state_sha256", "upstream_full_checkpoint_sha256_verified")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
