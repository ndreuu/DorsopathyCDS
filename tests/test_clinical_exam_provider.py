import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dorsopathy_modules.clinical_exam import ClinicalExamProvider
from dorsopathy_platform.api_contracts import FACT_IDS
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "clinical_exam"
CONCORDANCE = "clinical.imaging_clinical_concordance"
STUDY = "imaging.study_status"


class ClinicalExamProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))
        self.provider = ClinicalExamProvider()
        self.source = json.loads((EXAMPLES / "source_no_mri.json").read_text())

    def request(self, source, mri=None, job_id="exam-test-job"):
        refs = [self.store.add_bytes(json.dumps(source).encode(), "application/json")]
        if mri is not None:
            refs.append(self.store.add_bytes(json.dumps(mri).encode(), "application/json"))
        return {"schema_version": "1.0", "job_id": job_id, "episode_id": "test-episode",
                "module_id": "clinical.exam", "module_version": "0.1.0", "input_artifacts": refs,
                "parameters": {}, "protocol_id": None}

    def analyze(self, source=None, mri=None, job_id="exam-test-job"):
        request = self.request(self.source if source is None else source, mri, job_id)
        return self.provider.analyze(request, ProviderContext(self.store))

    def mri(self, origin="synthetic_demo", status="complete", episode_id="test-episode"):
        ref = self.store.add_bytes(b'{"synthetic_measurement":75}', "application/json")
        request = {"schema_version": "1.0", "job_id": "mri-test-job", "episode_id": episode_id,
                   "module_id": "mri.anatomy", "module_version": "0.1.0", "input_artifacts": [ref],
                   "parameters": {}, "protocol_id": None}
        obs = {"observation_id": observation_id_for(request["job_id"], "spine.canal.cross_sectional_area"),
               "concept": "spine.canal.cross_sectional_area", "assessment_status": "assessed",
               "value": 75, "unit": "mm2", "anatomy": {"region": "lumbar_spine", "side": "midline",
               "level": "L4-L5", "numbering_status": "needs_confirmation"}, "quality": "usable",
               "origin": origin, "method": "synthetic-measurement", "evidence_refs": [
                   {"artifact_id": ref["artifact_id"], "selector": {"kind": "json", "pointer": "/synthetic_measurement"}}],
               "model": None, "acquired_at": None, "coordinate_frame": None}
        return result_envelope(request, [obs], [ref], status)

    def reviewed_source(self, mri):
        source = json.loads((EXAMPLES / "source_reviewed.json").read_text())
        source["facts"][CONCORDANCE]["reviewed_result_id"] = mri["result_id"]
        return source

    def test_false_unknown_and_not_applicable_are_preserved(self):
        result = self.analyze()
        validate_result(result)
        observations = {o["concept"]: o for o in result["observations"]}
        self.assertEqual(set(observations), FACT_IDS)
        self.assertIs(observations["clinical.specific_cause_warning_signs_absent"]["value"], False)
        self.assertEqual(observations["clinical.radiculopathy_signs_and_positive_tension_tests"]["assessment_status"], "not_assessed")
        self.assertIsNone(observations[CONCORDANCE]["value"])
        self.assertEqual(observations[CONCORDANCE]["assessment_status"], "not_applicable")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(all(o["evidence_refs"] for o in result["observations"]))
        self.assertNotIn("assessment_ref", observations[CONCORDANCE])

    def test_running_or_unknown_mri_stays_unknown_without_result_artifact(self):
        self.source["facts"][STUDY].update(assessment_status="not_assessed", value=None)
        self.source["facts"][CONCORDANCE]["assessment_status"] = "not_assessed"
        result = self.analyze()
        self.assertEqual(result["status"], "partial")
        self.assertEqual({o["concept"]: o["value"] for o in result["observations"]}[STUDY], None)

    def test_known_false_concordance_requires_exact_available_reviewed_mri(self):
        for status in ("complete", "partial"):
            with self.subTest(status=status):
                mri = self.mri(status=status)
                result = self.analyze(self.reviewed_source(mri), mri)
                self.assertEqual(result["status"], "complete")
                observation = next(o for o in result["observations"] if o["concept"] == CONCORDANCE)
                self.assertIs(observation["value"], False)
                self.assertEqual(len(observation["evidence_refs"]), 2)
                self.assertEqual(observation["evidence_refs"][1]["selector"], {"kind": "json", "pointer": "/result_id"})

    def test_missing_wrong_episode_wrong_id_wrong_module_and_failed_result_rejected(self):
        mri = self.mri(); source = self.reviewed_source(mri)
        with self.assertRaises(ContractError):
            self.analyze(source)
        for mutation in ("episode", "id", "module", "version", "failed"):
            with self.subTest(mutation=mutation):
                candidate = json.loads(json.dumps(mri))
                if mutation == "episode": candidate["episode_id"] = "other-episode"
                elif mutation == "id": candidate["result_id"] = "other-result"
                elif mutation == "module": candidate["module_id"] = "emg.surface"
                elif mutation == "version": candidate["module_version"] = "2.0.0"
                else:
                    candidate.update(status="failed", observations=[], error={"code": "inference_failed", "message": "Unavailable."})
                with self.assertRaises(ContractError):
                    self.analyze(source, candidate)

    def test_not_performed_and_unassessed_review_contradictions_rejected(self):
        self.source["facts"][CONCORDANCE]["assessment_status"] = "not_assessed"
        with self.assertRaises(ContractError): self.analyze()
        self.source["facts"][STUDY]["value"] = "performed_result_available"
        self.source["facts"][CONCORDANCE]["reviewed_result_id"] = "fake-review"
        with self.assertRaises(ContractError): self.analyze()
        self.source["facts"][CONCORDANCE]["reviewed_result_id"] = None
        self.assertEqual(self.analyze()["status"], "partial")

    def test_assessed_clinical_values_do_not_coerce_strings_or_integers(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value):
                source = json.loads(json.dumps(self.source))
                source["facts"]["clinical.specific_cause_warning_signs_absent"]["value"] = value
                with self.assertRaises(ContractError): self.analyze(source)

    def test_modes_origins_author_time_and_exact_four_facts_are_validated(self):
        for field, value in (("mode", "clinician_input"), ("origin", []), ("acquired_at", "2026-10-09")):
            with self.subTest(field=field):
                source = json.loads(json.dumps(self.source)); source[field] = value
                with self.assertRaises(ContractError): self.analyze(source)
        source = json.loads(json.dumps(self.source)); source["author"]["role"] = "patient"
        with self.assertRaises(ContractError): self.analyze(source)
        self.source["facts"]["new.diagnosis"] = {"assessment_status": "assessed", "value": True}
        with self.assertRaises(ContractError): self.analyze()

    def test_real_clinician_origin_is_preserved(self):
        self.source.update(mode="clinician_input", origin="clinician_entered")
        self.assertEqual({o["origin"] for o in self.analyze()["observations"]}, {"clinician_entered"})

    def test_synthetic_form_with_real_mri_retains_origin_and_linkage_warning(self):
        mri = self.mri(origin="automatically_measured")
        result = self.analyze(self.reviewed_source(mri), mri)
        self.assertEqual({o["origin"] for o in result["observations"]}, {"synthetic_demo"})
        self.assertIn("mixed_origin_review", {w["code"] for w in result["warnings"]})
        self.assertEqual(len(result["artifacts"]), 2)

    def test_new_review_does_not_mutate_previous_result_or_snapshot(self):
        repository = SqliteRepository(Path(self.temp.name) / "db")
        repository.create_episode("test-episode")
        before_source = json.loads((EXAMPLES / "source_before_review.json").read_text())
        before_request = self.request(before_source, job_id="before-review")
        repository.create_job(before_request); repository.start_job("before-review")
        before = self.provider.analyze(before_request, ProviderContext(self.store))
        repository.complete_job(before)
        frozen = repository.create_snapshot("test-episode")
        mri = self.mri(); after_source = self.reviewed_source(mri)
        after_request = self.request(after_source, mri, job_id="after-review")
        repository.create_job(after_request); repository.start_job("after-review")
        repository.complete_job(self.provider.analyze(after_request, ProviderContext(self.store)))
        self.assertEqual(repository.get_snapshot(frozen["snapshot_id"]), frozen)
        self.assertEqual(repository.get_result(before["result_id"]), before)
        self.assertNotEqual(before["result_id"], repository.get_job("after-review")["result_id"])

    def test_shipped_synthetic_request_result_pairs_reproduce(self):
        for name in ("no_mri", "reviewed"):
            with self.subTest(name=name):
                request = json.loads((EXAMPLES / ("module_request_" + name + ".json")).read_text())
                refs = [self.store.add_bytes((EXAMPLES / ("source_" + name + ".json")).read_bytes(), "application/json")]
                if name == "reviewed":
                    refs.append(self.store.add_bytes((EXAMPLES / "reviewed_mri_result.json").read_bytes(), "application/json"))
                self.assertEqual(request["input_artifacts"], refs)
                expected = json.loads((EXAMPLES / ("module_result_" + name + ".json")).read_text())
                self.assertEqual(self.provider.analyze(request, ProviderContext(self.store)), expected)


if __name__ == "__main__":
    unittest.main()
