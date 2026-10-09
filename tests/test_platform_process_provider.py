import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dorsopathy_platform.orchestration import JobRunner
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.registry import ModuleRegistry
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository


WORKER = Path(__file__).parent / "fixtures/process_workers/protocol_worker.py"


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Orphaned killed children can briefly remain zombies until OS reaping.
    output = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(output) and not output.startswith("Z")


class ProcessProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifacts = ArtifactStore(self.root / "store")
        self.repository = SqliteRepository(self.root / "store")
        self.repository.create_episode("episode-test")
        self.registry = ModuleRegistry()
        self.runner = JobRunner(self.repository, self.artifacts, self.registry)
        self.workspaces = self.root / "workers"
        self.counter = 0

    def run_worker(self, mode="success", timeout=3, extra=(), parameters=None):
        self.counter += 1
        provider = ProcessProvider(
            ProviderSpec("test.process." + str(self.counter), "0.1.0", "Synthetic worker", synthetic_only=True),
            (sys.executable, str(WORKER), "--mode", mode) + extra,
            self.workspaces, timeout_seconds=timeout,
        )
        self.addCleanup(provider.close)
        self.registry.register(provider)
        reference = self.artifacts.add_bytes(b'{"synthetic":true}', "application/json")
        request = {
            "schema_version": "1.0", "job_id": "job-test-" + str(self.counter), "episode_id": "episode-test",
            "module_id": provider.spec.module_id, "module_version": provider.spec.module_version,
            "input_artifacts": [reference], "parameters": parameters or {}, "protocol_id": "synthetic-process-test",
        }
        self.runner.submit(request)
        return self.runner.execute(request["job_id"])

    def test_real_subprocess_imports_verified_derived_and_preserves_input_evidence(self):
        result = self.run_worker()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["observations"][0]["value"])
        derived = next(ref for ref in result["artifacts"] if ref["role"] == "derived")
        self.assertEqual(self.artifacts.read(derived), b"synthetic process artifact")
        self.assertEqual(list(self.workspaces.iterdir()), [])
        snapshot = self.repository.create_snapshot("episode-test")
        self.assertEqual(snapshot["results"][0], result)

    def test_worker_failure_has_safe_error_and_no_private_stderr(self):
        result = self.run_worker("fail")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "worker_exit_failed")
        self.assertNotIn("PRIVATE_PATIENT_TEXT", json.dumps(result))
        self.assertEqual(result["observations"], [])

    def test_worker_reported_failure_does_not_expose_its_error_text(self):
        result = self.run_worker("reported_failure")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "worker_reported_failure")
        self.assertEqual(result["warnings"], [])
        self.assertNotIn("PRIVATE_PATIENT", json.dumps(result))

    def test_malformed_oversized_duplicate_or_stdout_only_are_failed(self):
        for mode in ("malformed", "oversized", "duplicate_json", "stdout_only"):
            with self.subTest(mode=mode):
                result = self.run_worker(mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "invalid_module_result")
                self.assertNotIn("PRIVATE_PATIENT_TEXT", json.dumps(result))

    def test_wrong_context_unsafe_paths_and_corrupt_files_never_publish_observations(self):
        for mode in ("wrong_context", "traversal", "symlink", "corrupt", "undeclared"):
            with self.subTest(mode=mode):
                result = self.run_worker(mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["observations"], [])
                self.assertEqual(result["artifacts"], [])

    def test_timeout_kills_worker_and_child(self):
        pid_file = self.root / "child.pid"
        result = self.run_worker("timeout", timeout=0.5, extra=("--pid-file", str(pid_file)))
        self.assertEqual(result["error"]["code"], "worker_timeout")
        self.assertTrue(pid_file.exists(), "fixture must actually launch a child")
        pid = int(pid_file.read_text())
        self.assertFalse(process_alive(pid))
        self.assertEqual(list(self.workspaces.iterdir()), [])

    def test_normal_exit_also_cleans_leftover_child(self):
        pid_file = self.root / "child.pid"
        result = self.run_worker("child_exit", extra=("--pid-file", str(pid_file)))
        self.assertEqual(result["status"], "complete")
        self.assertFalse(process_alive(int(pid_file.read_text())))

    def test_parameters_cannot_override_trusted_executable(self):
        marker = self.root / "injected"
        result = self.run_worker(parameters={"command": ["touch", str(marker)], "shell": True})
        self.assertEqual(result["status"], "complete")
        self.assertFalse(marker.exists())

    def test_invalid_command_and_timeout_rejected(self):
        spec = ProviderSpec("test.process", "0.1.0", "Synthetic worker")
        for command in ("echo unsafe", (), (1,), ("bad\0command",)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                ProcessProvider(spec, command, self.workspaces)
        for timeout in (0, -1, float("nan"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ProcessProvider(spec, (sys.executable,), self.workspaces, timeout)


if __name__ == "__main__":
    unittest.main()
