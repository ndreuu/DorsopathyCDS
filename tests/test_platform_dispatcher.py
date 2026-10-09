from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from dorsopathy_platform.dispatcher import Dispatcher
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.registry import ModuleRegistry
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository


class RepositoryDouble(SqliteRepository):
    """Frozen T01 list_jobs interface over real persisted SQLite, test-only."""
    def list_jobs(self, episode_id=None, statuses=None):
        with self._connection() as connection:
            jobs = [self._job(row) for row in connection.execute("SELECT * FROM jobs ORDER BY job_id")]
        return [job for job in jobs if (episode_id is None or job["episode_id"] == episode_id)
                and (statuses is None or job["status"] in statuses)]


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifacts = ArtifactStore(self.root)
        self.repository = RepositoryDouble(self.root)
        self.repository.create_episode("episode-test")
        self.registry = ModuleRegistry()

    def persist(self, module_id, job_id):
        return self.repository.create_job({
            "schema_version": "1.0", "job_id": job_id, "episode_id": "episode-test",
            "module_id": module_id, "module_version": "0.1.0",
            "input_artifacts": [], "parameters": {}, "protocol_id": "synthetic-test",
        })

    def dispatcher(self, **kwargs):
        dispatcher = Dispatcher(self.repository, self.artifacts, self.registry, **kwargs)
        self.addCleanup(dispatcher.close)
        return dispatcher

    def wait_terminal(self, job_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = self.repository.get_job(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.01)
        self.fail("Persisted job did not reach a terminal state")

    def test_enqueue_returns_before_execution_and_deduplicates_delivery(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []
        class Provider:
            spec = ProviderSpec("test.blocking", "0.1.0", "Synthetic blocking", True)
            def analyze(self, request, context):
                calls.append(request["job_id"])
                entered.set()
                release.wait(3)
                return result_envelope(request, [], [])
        self.registry.register(Provider())
        self.persist("test.blocking", "job-one")
        dispatcher = self.dispatcher(recover=False)
        future = dispatcher.enqueue("job-one")
        self.assertTrue(entered.wait(2))
        self.assertFalse(future.done())
        self.assertIs(dispatcher.enqueue("job-one"), future)
        release.set()
        self.assertEqual(future.result(3)["status"], "complete")
        self.assertIsNone(dispatcher.enqueue("job-one"))
        self.assertEqual(calls, ["job-one"])

    def test_independent_cpu_jobs_run_concurrently(self):
        barrier = threading.Barrier(2)
        class Provider:
            spec = ProviderSpec("test.cpu", "0.1.0", "Synthetic CPU", True)
            def analyze(self, request, context):
                barrier.wait(timeout=3)
                return result_envelope(request, [], [])
        self.registry.register(Provider())
        self.persist("test.cpu", "job-cpu-a")
        self.persist("test.cpu", "job-cpu-b")
        dispatcher = self.dispatcher(max_workers=2, recover=False)
        futures = [dispatcher.enqueue(job) for job in ("job-cpu-a", "job-cpu-b")]
        self.assertEqual([future.result(4)["status"] for future in futures], ["complete", "complete"])

    def test_mri_serializes_without_occupying_cpu_lane(self):
        first_entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []
        class MRI:
            spec = ProviderSpec("mri.anatomy", "0.1.0", "Synthetic MRI", True)
            def analyze(self, request, context):
                calls.append(request["job_id"])
                if request["job_id"] == "job-mri-a":
                    first_entered.set()
                    release.wait(3)
                return result_envelope(request, [], [])
        class CPU:
            spec = ProviderSpec("test.cpu", "0.1.0", "Synthetic CPU", True)
            def analyze(self, request, context):
                return result_envelope(request, [], [])
        self.registry.register(MRI())
        self.registry.register(CPU())
        for module, job_id in (("mri.anatomy", "job-mri-a"), ("mri.anatomy", "job-mri-b"), ("test.cpu", "job-cpu")):
            self.persist(module, job_id)
        dispatcher = self.dispatcher(max_workers=1, recover=False)
        first = dispatcher.enqueue("job-mri-a")
        self.assertTrue(first_entered.wait(2))
        second = dispatcher.enqueue("job-mri-b")
        self.assertEqual(dispatcher.enqueue("job-cpu").result(2)["status"], "complete")
        self.assertFalse(second.done())
        self.assertEqual(self.repository.get_job("job-mri-b")["status"], "queued")
        release.set()
        self.assertEqual(first.result(3)["status"], "complete")
        self.assertEqual(second.result(3)["status"], "complete")
        self.assertEqual(calls, ["job-mri-a", "job-mri-b"])

    def test_restart_marks_running_interrupted_and_delivers_saved_queue(self):
        class Provider:
            spec = ProviderSpec("test.restart", "0.1.0", "Synthetic restart", True)
            def analyze(self, request, context):
                return result_envelope(request, [], [])
        self.registry.register(Provider())
        self.persist("test.restart", "job-interrupted")
        self.repository.start_job("job-interrupted")
        self.persist("test.restart", "job-saved")
        old = self.repository.create_snapshot("episode-test")
        dispatcher = self.dispatcher()
        interrupted = self.wait_terminal("job-interrupted")
        self.assertEqual(interrupted["status"], "failed")
        result = self.repository.get_result(interrupted["result_id"])
        self.assertEqual(result["error"]["code"], "worker_interrupted")
        self.assertEqual(self.wait_terminal("job-saved")["status"], "succeeded")
        self.assertEqual(self.repository.get_snapshot(old["snapshot_id"]), old)

    def test_unavailable_saved_provider_fails_instead_of_remaining_queued(self):
        self.persist("test.unavailable", "job-missing")
        self.dispatcher()
        job = self.wait_terminal("job-missing")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.repository.get_result(job["result_id"])["error"]["code"], "provider_unavailable")

    def test_two_dispatchers_cannot_execute_same_persisted_job_twice(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []
        class Provider:
            spec = ProviderSpec("test.cas", "0.1.0", "Synthetic CAS", True)
            def analyze(self, request, context):
                calls.append(request["job_id"])
                entered.set()
                release.wait(3)
                return result_envelope(request, [], [])
        self.registry.register(Provider())
        self.persist("test.cas", "job-cas")
        dispatchers = [self.dispatcher(recover=False), self.dispatcher(recover=False)]
        barrier = threading.Barrier(2)
        def deliver(dispatcher):
            barrier.wait(timeout=2)
            return dispatcher.enqueue("job-cas")
        with ThreadPoolExecutor(max_workers=2) as callers:
            deliveries = [callers.submit(deliver, dispatcher) for dispatcher in dispatchers]
            futures = [delivery.result(3) for delivery in deliveries]
        self.assertTrue(entered.wait(2))
        release.set()
        for future in futures:
            if future is not None:
                future.result(3)
        self.assertEqual(calls, ["job-cas"])
        self.assertEqual(self.repository.get_job("job-cas")["status"], "succeeded")

    def test_shutdown_kills_active_process_and_rejects_new_delivery(self):
        pid_file = self.root / "child.pid"
        worker = Path(__file__).parent / "fixtures/process_workers/protocol_worker.py"
        provider = ProcessProvider(ProviderSpec("test.process", "0.1.0", "Synthetic worker", True),
                                   (sys.executable, str(worker), "--mode", "timeout", "--pid-file", str(pid_file)),
                                   self.root / "workers", timeout_seconds=10)
        self.registry.register(provider)
        self.persist("test.process", "job-close")
        dispatcher = self.dispatcher(recover=False)
        future = dispatcher.enqueue("job-close")
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(pid_file.exists())
        dispatcher.close()
        self.assertEqual(future.result(2)["error"]["code"], "worker_interrupted")
        self.assertEqual(list((self.root / "workers").iterdir()), [])
        with self.assertRaises(RuntimeError):
            dispatcher.enqueue("job-close")

    def test_cleanup_failure_does_not_skip_other_providers(self):
        closed = []
        class Provider:
            def __init__(self, module_id, fail):
                self.spec = ProviderSpec(module_id, "0.1.0", "Synthetic cleanup", True)
                self.fail = fail
            def analyze(self, request, context):
                return result_envelope(request, [], [])
            def close(self):
                closed.append(self.spec.module_id)
                if self.fail:
                    raise ValueError("PRIVATE_PATIENT_TEXT")
        self.registry.register(Provider("test.close_a", True))
        self.registry.register(Provider("test.close_b", False))
        dispatcher = self.dispatcher(recover=False)
        with self.assertRaises(RuntimeError) as caught:
            dispatcher.close()
        self.assertEqual(closed, ["test.close_a", "test.close_b"])
        self.assertNotIn("PRIVATE_PATIENT_TEXT", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
