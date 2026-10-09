"""Stream limits, staging cleanup, scope and verified binary download."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dorsopathy_platform.storage import ArtifactStore, SqliteRepository, StorageError
from dorsopathy_platform.uploads import UploadService, UploadError


class BoundedStream(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        if size < 0 or size > 64 * 1024:
            raise AssertionError("Read must use a bounded buffer")
        self.read_sizes.append(size)
        return super().read(size)


class InterruptedStream(BoundedStream):
    def read(self, size=-1):
        if self.tell() > 0:
            raise OSError("sensitive stream detail")
        return super().read(size)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = SqliteRepository(self.root)
        self.repo.create_episode("episode-one")
        self.repo.create_episode("episode-other")
        self.store = ArtifactStore(self.root)
        self.uploads = UploadService(self.repo, self.store, max_bytes=2 * 1024 * 1024)

    def assert_empty_upload(self):
        self.assertEqual(self.repo.list_artifacts("episode-one"), [])
        if (self.root / "uploads").exists():
            self.assertEqual(list((self.root / "uploads").iterdir()), [])

    def test_large_upload_download_is_bounded_and_input_remains_open(self):
        data = b"synthetic-data\x00\xff" * 70000
        source = BoundedStream(data)
        ref = self.uploads.upload("episode-one", source, "application/octet-stream", len(data))
        self.assertGreater(len(data), 64 * 1024)
        self.assertEqual(ref["size_bytes"], len(data))
        self.assertEqual(ref["sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(source.closed)
        self.assertGreater(len(source.read_sizes), 1)
        looked_up, stream = self.uploads.open("episode-one", ref["artifact_id"])
        self.assertEqual(looked_up, ref)
        with stream:
            self.assertEqual(stream.tell(), 0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
            self.assertEqual(digest.hexdigest(), ref["sha256"])
        self.assertTrue(stream.closed)
        self.assertEqual(list((self.root / "uploads").iterdir()), [])

    def test_declared_limits_and_truncated_or_extra_stream_never_publish(self):
        for stream, declared in ((BytesIO(b"source"), 10), (BytesIO(b"source"), 3), (BytesIO(b"source"), -1), (BytesIO(b"source"), True), (BytesIO(b""), self.uploads.max_bytes + 1)):
            with self.subTest(declared=declared), self.assertRaises(UploadError):
                self.uploads.upload("episode-one", stream, "application/octet-stream", declared)
            self.assert_empty_upload()
        small = UploadService(self.repo, self.store, max_bytes=4)
        with self.assertRaises(UploadError) as failure:
            small.upload("episode-one", BytesIO(b"12345"), "application/octet-stream")
        self.assertEqual(failure.exception.code, "upload_too_large")
        self.assert_empty_upload()

    def test_interrupted_stream_is_cleaned_and_sensitive_error_hidden(self):
        stream = InterruptedStream(b"source" * 20000)
        with self.assertRaises(UploadError) as failure:
            self.uploads.upload("episode-one", stream, "application/octet-stream")
        self.assertNotIn("sensitive", str(failure.exception))
        self.assertFalse(stream.closed)
        self.assert_empty_upload()

    def test_deduplication_preserves_independent_episode_authorizations(self):
        def upload(episode):
            return self.uploads.upload(episode, BytesIO(b"shared"), "text/plain", 6)
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(upload, ["episode-one", "episode-other"] * 4))
        self.assertTrue(all(ref == refs[0] for ref in refs))
        self.assertEqual(len(list((self.root / "artifacts").iterdir())), 1)
        self.assertEqual(self.repo.list_artifacts("episode-one"), [refs[0]])
        self.assertEqual(self.repo.list_artifacts("episode-other"), [refs[0]])

    def test_download_scope_traversal_tampering_and_symlinks_rejected(self):
        ref = self.uploads.upload("episode-one", BytesIO(b"original"), "text/plain", 8)
        for episode, artifact_id in (("episode-other", ref["artifact_id"]), ("episode-one", "../outside"), ("episode-one", "missing")):
            with self.subTest(episode=episode), self.assertRaises(StorageError):
                self.uploads.open(episode, artifact_id)
        path = self.root / ref["storage_key"]
        path.write_bytes(b"modified")
        with self.assertRaises(StorageError):
            self.uploads.open("episode-one", ref["artifact_id"])
        external = self.root / "external"
        external.write_bytes(b"original")
        path.unlink()
        path.symlink_to(external)
        with self.assertRaises(StorageError):
            self.uploads.open("episode-one", ref["artifact_id"])
        self.assertEqual(external.read_bytes(), b"original")

    def test_catalog_metadata_tampering_fails_before_open(self):
        ref = self.uploads.upload("episode-one", BytesIO(b"source"), "text/plain")
        with sqlite3.connect(self.repo.path) as connection:
            connection.execute("UPDATE artifact_catalog SET ref_json=replace(ref_json,'6','7')")
        with self.assertRaises(StorageError):
            self.uploads.open("episode-one", ref["artifact_id"])

    def test_symlink_staging_directory_is_rejected(self):
        external = self.root / "external-directory"
        external.mkdir()
        (self.root / "uploads").symlink_to(external, target_is_directory=True)
        with self.assertRaises(UploadError):
            self.uploads.upload("episode-one", BytesIO(b"source"), "text/plain")
        self.assertEqual(list(external.iterdir()), [])
        self.assertEqual(self.repo.list_artifacts("episode-one"), [])

    def test_unregistered_blob_is_not_downloadable(self):
        ref = self.store.add_bytes(b"orphan", "text/plain")
        with self.assertRaises(StorageError):
            self.uploads.open("episode-one", ref["artifact_id"])
        self.assertEqual(self.store.read(ref), b"orphan")

    def test_file_backed_input_uses_same_stream_path(self):
        source = self.root / "selected-source.bin"
        data = b"file-backed-synthetic" * 5000
        source.write_bytes(data)
        with source.open("rb") as stream:
            ref = self.uploads.upload("episode-one", stream, "application/octet-stream", len(data))
            self.assertFalse(stream.closed)
        self.assertEqual(ref["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(list((self.root / "uploads").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
