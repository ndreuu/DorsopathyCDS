"""Storage integrity, independent job transitions and frozen provenance."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository, StorageError, TransitionError, ConflictError
from dorsopathy_platform.api_contracts import default_manifest


def request(job_id="job-one", episode_id="episode-one", artifacts=None):
    return {
        "schema_version": "1.0", "job_id": job_id, "episode_id": episode_id,
        "module_id": "synthetic-measurement", "module_version": "1.0",
        "input_artifacts": artifacts or [], "parameters": {"synthetic": True},
        "protocol_id": "synthetic-protocol",
    }


def result_for(module_request, result_id="result-one", status="complete"):
    artifacts = deepcopy(module_request["input_artifacts"])
    observations = []
    if artifacts and status != "failed":
        observations = [{
            "observation_id": "observation-one", "concept": "synthetic.measurement",
            "assessment_status": "assessed", "value": True, "unit": None,
            "anatomy": {"region": "lumbar", "side": "unspecified", "level": None, "numbering_status": "not_applicable"},
            "quality": "usable", "origin": "synthetic_demo", "method": "synthetic-fixture",
            "acquired_at": None, "coordinate_frame": None,
            "evidence_refs": [{"artifact_id": artifacts[0]["artifact_id"], "selector": {"field": "synthetic"}}], "model": None,
        }]
    return {
        "schema_version": "1.0", "result_id": result_id,
        **{field: module_request[field] for field in ("job_id", "episode_id", "module_id", "module_version")},
        "status": status, "observations": observations, "artifacts": artifacts, "warnings": [],
        "error": {"code": "synthetic_failure", "message": "Synthetic failure"} if status == "failed" else None,
    }


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ArtifactStore(self.root)

    def test_concurrent_identical_uploads_are_atomic_and_content_addressed(self):
        data = "Синтетический источник".encode("utf-8")
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(lambda _: self.store.add_bytes(data, "text/plain"), range(8)))
        self.assertTrue(all(ref == refs[0] for ref in refs))
        self.assertEqual(refs[0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(refs[0]["storage_key"], "artifacts/" + refs[0]["sha256"])
        self.assertEqual(self.store.read(refs[0]), data)
        self.assertEqual(len(list((self.root / "artifacts").iterdir())), 1)

    def test_corrupted_content_is_detected_and_never_overwritten(self):
        ref = self.store.add_bytes(b"original", "application/octet-stream")
        path = self.root / ref["storage_key"]
        path.write_bytes(b"modified")
        with self.assertRaises(StorageError):
            self.store.read(ref)
        with self.assertRaises(StorageError):
            self.store.add_bytes(b"original", "application/octet-stream")
        self.assertEqual(path.read_bytes(), b"modified")

    def test_reference_path_and_size_changes_are_rejected(self):
        ref = self.store.add_bytes(b"source", "application/octet-stream")
        for key in ("../outside", "/tmp/outside", "artifacts/../outside", "artifacts\\outside"):
            changed = dict(ref, storage_key=key)
            with self.subTest(key=key), self.assertRaises(StorageError):
                self.store.read(changed)
        with self.assertRaises(StorageError):
            self.store.read(dict(ref, size_bytes=ref["size_bytes"] + 1))

    def test_artifact_symlink_does_not_read_or_replace_external_file(self):
        ref = self.store.add_bytes(b"source", "application/octet-stream")
        external = self.root / "external-source"
        external.write_bytes(b"source")
        path = self.root / ref["storage_key"]
        path.unlink()
        path.symlink_to(external)
        with self.assertRaises(StorageError):
            self.store.read(ref)
        with self.assertRaises(StorageError):
            self.store.add_bytes(b"source", "application/octet-stream")
        self.assertEqual(external.read_bytes(), b"source")

    def test_symlink_artifact_directory_is_rejected(self):
        external = self.root / "external-directory"
        external.mkdir()
        (self.root / "artifacts").symlink_to(external, target_is_directory=True)
        with self.assertRaises(StorageError):
            self.store.add_bytes(b"source", "application/octet-stream")
        self.assertEqual(list(external.iterdir()), [])

    def test_file_import_can_be_consumed_as_a_rewound_binary_stream(self):
        data = b"synthetic-file-data" * 70000
        source = self.root / "provider-output.bin"
        source.write_bytes(data)
        ref = self.store.add_file(source, "application/octet-stream", role="derived")
        self.assertEqual(ref["role"], "derived")
        self.assertEqual(ref["size_bytes"], len(data))
        self.assertEqual(ref["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.store.add_file(source, "application/octet-stream", role="derived"), ref)
        with self.store.open(ref) as stream:
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(stream.read(37), data[:37])
            stream.seek(len(data) - 21)
            self.assertEqual(stream.read(21), data[-21:])
        self.assertTrue(stream.closed)
        self.assertEqual(self.store.read(ref), data)

    def test_open_verifies_same_size_tampering_before_returning_a_handle(self):
        source = self.root / "provider-output.bin"
        source.write_bytes(b"original")
        ref = self.store.add_file(source, "application/octet-stream")
        stored_path = self.root / ref["storage_key"]
        stored_path.write_bytes(b"modified")
        self.assertEqual(stored_path.stat().st_size, ref["size_bytes"])
        with self.assertRaises(StorageError):
            self.store.open(ref)
        with self.assertRaises(StorageError):
            self.store.add_file(source, "application/octet-stream")
        self.assertEqual(stored_path.read_bytes(), b"modified")


class SqliteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = SqliteRepository(self.root)
        self.repo.create_episode("episode-one")

    def finish(self, module_request, result_id="result-one", status="complete"):
        self.repo.create_job(module_request)
        self.repo.start_job(module_request["job_id"])
        result = result_for(module_request, result_id, status)
        job = self.repo.complete_job(result)
        return result, job

    def test_episode_creation_is_idempotent_and_unknown_ids_are_safe(self):
        episode = self.repo.create_episode("episode-one")
        self.assertEqual(episode, self.repo.get_episode("episode-one"))
        for operation in (self.repo.get_episode, self.repo.get_job, self.repo.get_result, self.repo.get_snapshot):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(StorageError) as failure:
                    operation("unknown-sensitive-token")
                self.assertNotIn("unknown-sensitive-token", str(failure.exception))

    def test_job_request_is_idempotent_by_canonical_hash_and_immutable(self):
        payload = request()
        original = deepcopy(payload)
        created = self.repo.create_job(payload)
        reordered = dict(reversed(list(original.items())))
        self.assertEqual(self.repo.create_job(reordered), created)
        payload["parameters"]["synthetic"] = False
        self.assertEqual(self.repo.get_job(original["job_id"])["request"], original)
        with self.assertRaises(StorageError):
            self.repo.create_job(payload)
        self.assertEqual(self.repo.get_job(original["job_id"])["status"], "queued")

    def test_state_transitions_and_result_publication_are_immutable(self):
        payload = request()
        self.repo.create_job(payload)
        outcome = result_for(payload)
        with self.assertRaises(TransitionError):
            self.repo.complete_job(outcome)
        self.assertEqual(self.repo.start_job(payload["job_id"])["status"], "running")
        with self.assertRaises(TransitionError):
            self.repo.start_job(payload["job_id"])
        job = self.repo.complete_job(outcome)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result_id"], outcome["result_id"])
        with self.assertRaises(TransitionError):
            self.repo.complete_job(outcome)
        outcome["warnings"].append({"code": "changed", "message": "Changed caller value"})
        self.assertEqual(self.repo.get_result("result-one")["warnings"], [])
        self.assertEqual(self.repo.create_job(payload)["status"], "succeeded")

    def test_context_conflict_rolls_back_result_without_advancing_job(self):
        payload = request()
        self.repo.create_job(payload)
        self.repo.start_job(payload["job_id"])
        outcome = result_for(payload)
        outcome["module_version"] = "wrong-version"
        with self.assertRaises(ContractError):
            self.repo.complete_job(outcome)
        self.assertEqual(self.repo.get_job(payload["job_id"])["status"], "running")
        self.assertEqual(self.repo.list_results(payload["episode_id"]), [])

    def test_independent_workers_can_run_and_complete_concurrently(self):
        barrier = threading.Barrier(2)

        def worker(index):
            payload = request("job-" + str(index))
            self.repo.create_job(payload)
            self.repo.start_job(payload["job_id"])
            barrier.wait(timeout=5)
            return self.repo.complete_job(result_for(payload, "result-" + str(index)))

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(worker, (1, 2)))
        self.assertEqual([job["status"] for job in jobs], ["succeeded", "succeeded"])
        self.assertEqual([result["result_id"] for result in self.repo.list_results("episode-one")], ["result-1", "result-2"])

    def test_only_one_worker_can_start_the_same_job(self):
        self.repo.create_job(request())
        barrier = threading.Barrier(2)

        def worker(_):
            barrier.wait(timeout=5)
            try:
                return self.repo.start_job("job-one")["status"]
            except TransitionError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, (1, 2)))
        self.assertCountEqual(outcomes, ["running", "rejected"])

    def test_all_terminal_statuses_are_retained_in_deterministic_order(self):
        for index, status in enumerate(("failed", "partial", "complete")):
            _, job = self.finish(request("job-" + str(index)), "result-" + str(index), status)
            self.assertEqual(job["status"], {"failed": "failed", "partial": "partial", "complete": "succeeded"}[status])
        self.assertEqual([item["status"] for item in self.repo.list_results("episode-one")], ["failed", "partial", "complete"])

    def test_result_id_conflict_preserves_the_running_job_and_prior_result(self):
        prior, _ = self.finish(request())
        other = request("job-two")
        self.repo.create_job(other)
        self.repo.start_job(other["job_id"])
        with self.assertRaises(StorageError):
            self.repo.complete_job(result_for(other, prior["result_id"]))
        self.assertEqual(self.repo.get_job(other["job_id"])["status"], "running")
        self.assertEqual(self.repo.get_result(prior["result_id"]), prior)

    def test_snapshot_freezes_exact_result_provenance_despite_new_results(self):
        artifact = ArtifactStore(self.root).add_bytes(b'{"synthetic": true}', "application/json")
        first, _ = self.finish(request(artifacts=[artifact]))
        snapshot = self.repo.create_snapshot("episode-one")
        original = deepcopy(snapshot)
        self.assertEqual(snapshot["results"], [first])
        self.assertEqual(self.repo.create_snapshot("episode-one", ["result-one"]), original)
        self.finish(request("job-two"), "result-two")
        snapshot["results"][0]["observations"][0]["evidence_refs"][0]["selector"]["field"] = "mutated"
        self.assertEqual(self.repo.get_snapshot(original["snapshot_id"]), original)
        updated = self.repo.create_snapshot("episode-one")
        self.assertEqual(updated["result_ids"], ["result-one", "result-two"])
        self.assertNotEqual(updated["snapshot_id"], original["snapshot_id"])

    def test_direct_completion_carries_used_input_evidence_into_frozen_result(self):
        artifacts = ArtifactStore(self.root)
        used = artifacts.add_bytes(b'{"synthetic": true}', "application/json")
        unused = artifacts.add_bytes(b'{"unused": true}', "application/json")
        payload = request(artifacts=[used, unused])
        self.repo.create_job(payload)
        self.repo.start_job(payload["job_id"])
        outcome = result_for(payload)
        outcome["artifacts"] = []
        caller_copy = deepcopy(outcome)
        with self.assertRaises(ContractError):
            validate_result(outcome)
        self.repo.complete_job(outcome)
        self.assertEqual(outcome, caller_copy)
        persisted = self.repo.get_result(outcome["result_id"])
        self.assertEqual(persisted["artifacts"], [used])
        self.assertEqual(validate_result(persisted), persisted)
        snapshot = self.repo.create_snapshot(payload["episode_id"])
        self.assertEqual(snapshot["results"], [persisted])
        self.assertEqual(validate_result(snapshot["results"][0]), persisted)

    def test_snapshot_rejects_foreign_missing_and_duplicate_results(self):
        self.finish(request())
        self.repo.create_episode("episode-other")
        self.finish(request("job-other", "episode-other"), "result-other")
        for selected in (["result-one", "result-one"], ["result-other"], ["missing-result"]):
            with self.subTest(selected=selected), self.assertRaises(StorageError):
                self.repo.create_snapshot("episode-one", selected)
        empty_one = self.repo.create_snapshot("episode-one", [])
        empty_other = self.repo.create_snapshot("episode-other", [])
        self.assertNotEqual(empty_one["snapshot_id"], empty_other["snapshot_id"])


class CatalogAndWorkflowTests(unittest.TestCase):
    setUp = SqliteRepositoryTests.setUp

    def manifest_for(self, payloads):
        modules = [{
            "module_id": payload["module_id"], "module_version": payload["module_version"],
            "input_artifact_ids": [ref["artifact_id"] for ref in payload["input_artifacts"]],
            "parameters": payload["parameters"],
        } for payload in payloads]
        manifest = default_manifest("episode-one", modules)
        manifest["protocol_id"] = "synthetic-protocol"
        return manifest

    def test_catalog_is_scoped_and_content_deduplicates_without_metadata_overwrite(self):
        ref = ArtifactStore(self.root).add_bytes(b"shared", "text/plain")
        self.assertEqual(self.repo.register_artifact("episode-one", ref), ref)
        ref["role"] = "derived"
        with self.assertRaises(ConflictError):
            self.repo.register_artifact("episode-one", ref)
        self.repo.create_episode("episode-other")
        with self.assertRaises(StorageError):
            self.repo.get_artifact("episode-other", ref["artifact_id"])
        self.repo.register_artifact("episode-other", ref)
        self.assertEqual(self.repo.get_artifact("episode-one", ref["artifact_id"])["role"], "source")
        self.assertEqual(self.repo.list_artifacts("episode-other"), [ref])
        self.assertEqual(len(list((self.root / "artifacts").iterdir())), 1)

    def test_media_types_cannot_inject_http_headers(self):
        store = ArtifactStore(self.root)
        for media_type in ("text/plain\r\nX-Injected: yes", "text/\x00plain", "x" * 256, "text/π"):
            with self.subTest(media_type=media_type), self.assertRaises(StorageError):
                store.add_bytes(b"source", media_type)
        self.assertEqual(store.add_bytes(b"source", "text/plain; charset=utf-8")["media_type"], "text/plain; charset=utf-8")

    def test_concurrent_run_starts_publish_one_job_and_retries_ignore_new_ids(self):
        manifest = self.manifest_for([request()])
        barrier = threading.Barrier(6)
        def start(index):
            barrier.wait(timeout=5)
            return self.repo.create_run("episode-one", "same-key", manifest, [request("job-" + str(index))], "run-" + str(index))
        with ThreadPoolExecutor(max_workers=6) as pool:
            runs = list(pool.map(start, range(6)))
        self.assertTrue(all(run == runs[0] for run in runs))
        self.assertEqual(len(self.repo.list_jobs()), 1)
        self.assertEqual(self.repo.create_run("episode-one", "same-key", dict(reversed(list(manifest.items())))), runs[0])
        self.assertEqual(self.repo.list_pending_runs(), [runs[0]])
        changed = deepcopy(manifest)
        changed["modules"][0]["parameters"]["synthetic"] = False
        with self.assertRaises(ConflictError):
            self.repo.create_run("episode-one", "same-key", changed)
        self.assertEqual(self.repo.list_pending_runs(), [runs[0]])

    def test_workflow_request_mismatch_and_job_collision_roll_back_everything(self):
        one, two = request("job-new"), request("job-used")
        two["module_id"] = "other-measurement"
        self.repo.create_job(two)
        manifest = self.manifest_for([one, two])
        with self.assertRaises(ConflictError):
            self.repo.create_run("episode-one", "key", manifest, [one, two])
        self.assertEqual([job["job_id"] for job in self.repo.list_jobs()], ["job-used"])
        self.assertEqual(self.repo.list_pending_runs(), [])
        for field, value in (("episode_id", "episode-other"), ("module_version", "other"), ("parameters", {}), ("protocol_id", None)):
            altered = dict(one, **{field: value})
            with self.subTest(field=field), self.assertRaises(StorageError):
                self.repo.create_run("episode-one", "key", self.manifest_for([one]), [altered])
        with self.assertRaises(StorageError):
            self.repo.create_run("episode-one", "key", self.manifest_for([one]))
        self.assertEqual(self.repo.list_pending_runs(), [])

    def test_new_run_requires_catalog_metadata_and_reused_result_episode(self):
        ref = ArtifactStore(self.root).add_bytes(b"source", "application/octet-stream")
        payload = request(artifacts=[ref])
        manifest = self.manifest_for([payload])
        with self.assertRaises(StorageError):
            self.repo.create_run("episode-one", "key", manifest, [payload])
        self.repo.register_artifact("episode-one", ref)
        with self.assertRaises(StorageError):
            self.repo.create_run("episode-one", "key", manifest, [request(artifacts=[dict(ref, media_type="text/plain")])])
        self.repo.create_run("episode-one", "key", manifest, [payload])
        self.repo.start_job(payload["job_id"])
        self.repo.complete_job(result_for(payload))
        reused = dict(manifest, modules=[], reuse_result_ids=["result-one"])
        self.assertEqual(self.repo.create_run("episode-one", "reuse-key", reused)["job_ids"], [])
        self.repo.create_episode("episode-other")
        with self.assertRaises(StorageError):
            self.repo.create_run("episode-other", "key", dict(reused, episode_id="episode-other"))

    def test_terminal_run_cas_preserves_winning_report_and_survives_reopen(self):
        payload = request()
        run = self.repo.create_run("episode-one", "key", self.manifest_for([payload]), [payload])
        self.repo.start_job(payload["job_id"])
        self.repo.complete_job(result_for(payload))
        snapshot = self.repo.create_snapshot("episode-one")
        store = ArtifactStore(self.root)
        refs = [self.repo.register_artifact("episode-one", store.add_bytes(str(index).encode(), "application/json", role="derived")) for index in range(6)]
        barrier = threading.Barrier(6)
        def finalize(index):
            barrier.wait(timeout=5)
            return self.repo.finalize_run(run["run_id"], snapshot["snapshot_id"], refs[index], "complete" if index % 2 else "partial")
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(finalize, range(6)))
        self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))
        self.assertEqual(outcomes[0]["snapshot_id"], snapshot["snapshot_id"])
        self.assertIn(outcomes[0]["report_artifact"], refs)
        self.assertEqual(self.repo.list_pending_runs(), [])
        reopened = SqliteRepository(self.root)
        self.assertEqual(reopened.get_run(run["run_id"]), outcomes[0])
        self.assertEqual(reopened.finalize_run(run["run_id"], snapshot["snapshot_id"], refs[-1], "failed"), outcomes[0])

    def test_finalize_rejects_unregistered_foreign_and_source_report(self):
        payload = request()
        run = self.repo.create_run("episode-one", "key", self.manifest_for([payload]), [payload])
        store = ArtifactStore(self.root)
        report = store.add_bytes(b"report", "application/json", role="derived")
        snapshot = self.repo.create_snapshot("episode-one", [])
        self.repo.create_episode("episode-other")
        foreign = self.repo.create_snapshot("episode-other", [])
        with self.assertRaises(StorageError):
            self.repo.finalize_run(run["run_id"], snapshot["snapshot_id"], report, "complete")
        self.repo.register_artifact("episode-one", report)
        for snap_id, ref in ((foreign["snapshot_id"], report), (snapshot["snapshot_id"], dict(report, size_bytes=100)), (snapshot["snapshot_id"], dict(report, role="source"))):
            with self.subTest(snapshot=snap_id), self.assertRaises(StorageError):
                self.repo.finalize_run(run["run_id"], snap_id, ref, "complete")
        self.assertEqual(self.repo.get_run(run["run_id"])["status"], "running")

    def test_recovery_job_filters_are_scoped_and_ordered(self):
        self.repo.create_job(request("job-z"))
        self.repo.create_job(request("job-a"))
        self.repo.start_job("job-z")
        self.assertEqual([job["job_id"] for job in self.repo.list_jobs(statuses=["queued", "running"])], ["job-a", "job-z"])
        self.assertEqual([job["job_id"] for job in self.repo.list_jobs("episode-one", ["running"])], ["job-z"])
        self.assertEqual(self.repo.list_jobs(statuses=[]), [])
        for statuses in ("queued", ["unknown"], [None]):
            with self.subTest(statuses=statuses), self.assertRaises(StorageError):
                self.repo.list_jobs(statuses=statuses)


if __name__ == "__main__":
    unittest.main()
