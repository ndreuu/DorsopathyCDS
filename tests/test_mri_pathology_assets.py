"""Check worker asset integrity with tiny synthetic, non-model files."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dorsopathy_platform.contracts import ContractError
from dorsopathy_workers.mri_pathology import worker


class MRIPathologyAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "synthetic-upstream"
        self.weights = self.root / "spinenet" / "weights"
        self.contents = {}
        self.manifest = {"schema_version": "spinenet-assets.v1", "upstream_commit": worker.UPSTREAM_COMMIT,
                         "license_url": "https://github.com/rwindsor1/SpineNet/blob/" + worker.UPSTREAM_COMMIT + "/LICENCE.md",
                         "files": {}}
        # These are opaque test bytes, not loadable model checkpoints. Git
        # identity is mocked; no network, model loader or existing weights run.
        for relative in worker.WEIGHT_FILES:
            contents = ("synthetic-integrity-fixture:" + relative).encode("ascii")
            path = self.weights / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            self.contents[relative] = contents
            self.manifest["files"][relative] = {
                "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents),
                "url": "https://www.robots.ox.ac.uk/~vgg/research/spinenet/weights/" + relative,
            }
        self.save_manifest()

    def save_manifest(self):
        (self.weights / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def verify(self, commit=None, dirty=False):
        with patch.object(worker.subprocess, "check_output", return_value=(commit or worker.UPSTREAM_COMMIT) + "\n") as git_head, \
             patch.object(worker.subprocess, "run", return_value=SimpleNamespace(returncode=1 if dirty else 0)) as git_diff:
            result = worker.verify_assets(self.root)
            git_head.assert_called_once_with(["git", "-C", str(self.root.resolve()), "rev-parse", "HEAD"], text=True, timeout=15)
            git_diff.assert_called_once_with(["git", "-C", str(self.root.resolve()), "diff", "--quiet", "HEAD", "--", "spinenet"], check=False, timeout=15)
            return result

    def test_verified_provenance_uses_actual_file_hashes_sizes_and_official_urls(self):
        before = {relative: (self.weights / relative).read_bytes() for relative in worker.WEIGHT_FILES}
        result = self.verify()
        self.assertEqual(set(result), set(worker.WEIGHT_FILES))
        for relative, provenance in result.items():
            self.assertEqual(provenance["sha256"], hashlib.sha256(self.contents[relative]).hexdigest())
            self.assertEqual(provenance["size_bytes"], len(self.contents[relative]))
            self.assertEqual(provenance["url"], self.manifest["files"][relative]["url"])
            self.assertEqual((self.weights / relative).read_bytes(), before[relative])

    def test_same_size_checkpoint_tampering_is_rejected_for_each_model(self):
        for relative in worker.WEIGHT_FILES:
            with self.subTest(relative=relative):
                path = self.weights / relative
                original = self.contents[relative]
                path.write_bytes(b"X" + original[1:])
                with self.assertRaises(ContractError):
                    self.verify()
                path.write_bytes(original)

    def test_manifest_size_mismatch_is_rejected_independently_of_hash(self):
        relative = worker.WEIGHT_FILES[0]
        self.manifest["files"][relative]["size_bytes"] += 1
        self.save_manifest()
        with self.assertRaises(ContractError):
            self.verify()

    def test_extra_checkpoint_cannot_silently_change_upstream_model_selection(self):
        extra = (self.weights / worker.WEIGHT_FILES[0]).parent / "newer.pt"
        extra.write_bytes(b"different-synthetic-checkpoint")
        with self.assertRaises(ContractError):
            self.verify()

    def test_missing_checkpoint_and_symlink_checkpoint_are_rejected(self):
        path = self.weights / worker.WEIGHT_FILES[0]
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ContractError):
            self.verify()
        target = self.root / "synthetic-link-target"
        target.write_bytes(original)
        path.symlink_to(target)
        with self.assertRaises(ContractError):
            self.verify()

    def test_wrong_git_commit_is_rejected_before_model_loading(self):
        with self.assertRaises(ContractError):
            self.verify(commit="0" * 40)

    def test_tracked_source_modification_is_rejected(self):
        with self.assertRaises(ContractError):
            self.verify(dirty=True)

    def test_manifest_protocol_commit_and_checkpoint_keys_must_match(self):
        original = copy.deepcopy(self.manifest)
        invalid = []
        for field, value in (("schema_version", "spinenet-assets.v2"), ("upstream_commit", "0" * 40),
                             ("license_url", "https://example.invalid/unverified-license"), ("files", {}), ("files", [])):
            manifest = copy.deepcopy(original)
            manifest[field] = value
            invalid.append(manifest)
        manifest = copy.deepcopy(original)
        manifest["files"]["grading/unsupported.pt"] = manifest["files"].pop(worker.WEIGHT_FILES[0])
        invalid.append(manifest)
        manifest = copy.deepcopy(original)
        del manifest["schema_version"]
        invalid.append(manifest)
        for index, manifest in enumerate(invalid):
            with self.subTest(case=index):
                self.manifest = manifest
                self.save_manifest()
                with self.assertRaises(ContractError):
                    self.verify()

    def test_checkpoint_provenance_entry_fields_and_official_url_are_mandatory(self):
        relative = worker.WEIGHT_FILES[0]
        original = copy.deepcopy(self.manifest["files"][relative])
        invalid = []
        for field in ("url", "sha256", "size_bytes"):
            entry = copy.deepcopy(original)
            del entry[field]
            invalid.append(entry)
        for field, value in (("url", "https://example.invalid/unverified-checkpoint"), ("url", None),
                             ("sha256", "g" * 64), ("sha256", "a" * 63),
                             ("size_bytes", True), ("size_bytes", 0), ("size_bytes", -1),
                             ("size_bytes", 1024**3 + 1)):
            entry = copy.deepcopy(original)
            entry[field] = value
            invalid.append(entry)
        entry = copy.deepcopy(original)
        entry["unverified"] = True
        invalid.append(entry)
        invalid.extend([None, []])
        for index, entry in enumerate(invalid):
            with self.subTest(case=index):
                self.manifest["files"][relative] = entry
                self.save_manifest()
                with self.assertRaises(ContractError):
                    self.verify()

    def test_manifest_json_must_be_object_and_finite_unambiguous(self):
        for contents in ('[]', '{"schema_version":"spinenet-assets.v1","schema_version":"spinenet-assets.v1"}',
                         '{"unverified":NaN}'):
            with self.subTest(contents=contents):
                (self.weights / "manifest.json").write_text(contents, encoding="utf-8")
                with self.assertRaises(ContractError):
                    self.verify()

    def test_manifest_cannot_be_oversized_or_a_symlink(self):
        path = self.weights / "manifest.json"
        contents = path.read_bytes()
        path.write_bytes(contents + b' ' * 65537)
        with self.assertRaises(ContractError):
            self.verify()
        path.unlink()
        target = self.root / "synthetic-manifest-target.json"
        target.write_bytes(contents)
        path.symlink_to(target)
        with self.assertRaises(ContractError):
            self.verify()

    def test_worker_import_and_asset_verification_require_no_model_dependencies(self):
        script = """
import sys
from types import SimpleNamespace
from unittest.mock import patch
blocked = {'torch', 'torchvision', 'numpy', 'SimpleITK', 'pydicom', 'cv2', 'spinenet', 'PIL'}
class NoModelImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('model dependency imported during asset verification')
sys.meta_path.insert(0, NoModelImports())
from dorsopathy_workers.mri_pathology import worker
with patch.object(worker.subprocess, 'check_output', return_value=worker.UPSTREAM_COMMIT), \
     patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
    actual = worker.verify_assets(sys.argv[1])
assert set(actual) == set(worker.WEIGHT_FILES)
assert not (blocked & set(sys.modules))
"""
        completed = subprocess.run([sys.executable, "-c", script, str(self.root)],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
