from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from dorsopathy_platform.orchestration import JobRunner
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.registry import ModuleRegistry, RegistryError
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository, TransitionError
from dorsopathy_modules.demo.complaints import DemoComplaintsProvider
from dorsopathy_modules.demo.mri import DemoMRIProvider


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ArtifactStore(Path(self.directory.name))
        self.repository = SqliteRepository(Path(self.directory.name))
        self.repository.create_episode("episode-test")
        self.registry = ModuleRegistry()
        self.runner = JobRunner(self.repository, self.store, self.registry)

    def request(self, provider, job_id, fixture=None):
        self.registry.register(provider)
        inputs = [] if fixture is None else [self.store.add_bytes(json.dumps(fixture).encode(), "application/json")]
        request = {
            "schema_version": "1.0", "job_id": job_id, "episode_id": "episode-test",
            "module_id": provider.spec.module_id, "module_version": provider.spec.module_version,
            "input_artifacts": inputs, "parameters": {}, "protocol_id": "synthetic-test-v1",
        }
        self.runner.submit(request)
        return request

    def test_independent_jobs_actually_execute_concurrently(self):
        barrier = threading.Barrier(2)

        class WaitingProvider:
            def __init__(self, module_id):
                self.spec = ProviderSpec(module_id, "0.1.0", "Barrier fixture", synthetic_only=True)

            def analyze(self, request, context):
                barrier.wait(timeout=5)
                return result_envelope(request, [], [])

        self.request(WaitingProvider("demo.barrier_a"), "job-a")
        self.request(WaitingProvider("demo.barrier_b"), "job-b")
        results = self.runner.execute_many(["job-a", "job-b"], max_workers=2)
        self.assertEqual([r["status"] for r in results], ["complete", "complete"])

    def test_provider_failure_does_not_cancel_another_module(self):
        class FailingProvider:
            spec = ProviderSpec("demo.failure", "0.1.0", "Failure fixture", synthetic_only=True)

            def analyze(self, request, context):
                raise ValueError("PRIVATE_TEST_CONTENT")

        self.request(FailingProvider(), "job-failed")
        self.request(DemoComplaintsProvider(), "job-good", {"synthetic": True, "pain_intensity": 6, "side": "left", "region": "lower_back"})
        results = self.runner.execute_many(["job-failed", "job-good"])
        self.assertEqual([r["status"] for r in results], ["failed", "complete"])
        self.assertNotIn("PRIVATE_TEST_CONTENT", json.dumps(results))
        self.assertEqual(results[0]["observations"], [])

    def test_wrong_episode_result_is_rejected(self):
        class WrongEpisodeProvider:
            spec = ProviderSpec("demo.wrong_episode", "0.1.0", "Identity fixture", synthetic_only=True)

            def analyze(self, request, context):
                result = result_envelope(request, [], [])
                result["episode_id"] = "another-episode"
                return result

        self.request(WrongEpisodeProvider(), "job-invalid")
        result = self.runner.execute("job-invalid")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "invalid_module_result")

    def test_demo_provider_cannot_present_observations_as_real_measurements(self):
        class FalseOriginProvider(DemoMRIProvider):
            def analyze(self, request, context):
                result = super().analyze(request, context)
                result["observations"][0]["origin"] = "automatically_measured"
                return result

        self.request(FalseOriginProvider(), "job-mislabeled", {"synthetic": True, "area_mm2": 104, "level": "L4-L5"})
        result = self.runner.execute("job-mislabeled")
        self.assertEqual(result["status"], "failed")

    def test_tampered_input_prevents_provider_output(self):
        request = self.request(DemoMRIProvider(), "job-tampered", {"synthetic": True, "area_mm2": 104, "level": "L4-L5"})
        path = Path(self.directory.name) / request["input_artifacts"][0]["storage_key"]
        path.write_bytes(b"tampered fixture")
        result = self.runner.execute("job-tampered")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], [])

    def test_ambiguous_questionnaire_json_never_produces_a_score(self):
        request = self.request(DemoComplaintsProvider(), "job-ambiguous")
        reference = self.store.add_bytes(
            b'{"synthetic":false,"synthetic":true,"pain_intensity":6,"region":"lower_back","side":"left"}',
            "application/json",
        )
        # Submit a fresh job: stored requests are immutable.
        request["job_id"] = "job-ambiguous-input"
        request["input_artifacts"] = [reference]
        self.runner.submit(request)
        result = self.runner.execute(request["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], [])

    def test_result_contains_referenced_input_artifacts(self):
        class InputOnlyEvidenceProvider(DemoMRIProvider):
            def analyze(self, request, context):
                result = super().analyze(request, context)
                result["artifacts"] = []
                return result

        request = self.request(InputOnlyEvidenceProvider(), "job-source-evidence", {"synthetic": True, "area_mm2": 104, "level": "L4-L5"})
        result = self.runner.execute(request["job_id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["artifacts"], request["input_artifacts"])
        snapshot = self.repository.create_snapshot("episode-test")
        self.assertEqual(snapshot["results"][0]["artifacts"], request["input_artifacts"])

    def test_job_cannot_be_executed_twice(self):
        self.request(DemoMRIProvider(), "job-single", {"synthetic": True, "area_mm2": 104, "level": "L4-L5"})
        self.runner.execute("job-single")
        with self.assertRaises(TransitionError):
            self.runner.execute("job-single")

    def test_registry_requires_exact_provider_version(self):
        self.registry.register(DemoMRIProvider())
        with self.assertRaises(RegistryError):
            self.registry.register(DemoMRIProvider())
        with self.assertRaises(RegistryError):
            self.registry.get("demo.mri_anatomy", "another-version")

    def test_old_snapshot_is_stable_after_another_module_finishes(self):
        self.request(DemoComplaintsProvider(), "job-first", {"synthetic": True, "pain_intensity": 6, "side": "left", "region": "lower_back"})
        self.runner.execute("job-first")
        first = self.repository.create_snapshot("episode-test")
        preserved = deepcopy(first)
        self.request(DemoMRIProvider(), "job-second", {"synthetic": True, "area_mm2": 104, "level": "L4-L5"})
        self.runner.execute("job-second")
        second = self.repository.create_snapshot("episode-test")
        self.assertEqual(self.repository.get_snapshot(first["snapshot_id"]), preserved)
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(len(first["results"]), 1)
        self.assertEqual(len(second["results"]), 2)


if __name__ == "__main__":
    unittest.main()
