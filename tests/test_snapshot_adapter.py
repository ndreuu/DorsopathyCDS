"""Clinical evidence identity, selection, linkage and applicability policies."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from dorsopathy_cds.snapshot_adapter import build_case
from dorsopathy_cds.service import evaluate_case
from dorsopathy_modules.clinical_exam import ClinicalExamProvider
from dorsopathy_platform.api_contracts import canonical_bytes, default_manifest
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_platform.storage import ArtifactStore

EPISODE = "synthetic-report-episode"
STUDY = "imaging.study_status"
CONCORDANCE = "clinical.imaging_clinical_concordance"
WARNING = "clinical.specific_cause_warning_signs_absent"
RADIC = "clinical.radiculopathy_signs_and_positive_tension_tests"


def make_snapshot(results):
    results = sorted(deepcopy(results), key=lambda result: result["result_id"])
    content = {"schema_version": "1.0", "episode_id": EPISODE,
               "result_ids": [result["result_id"] for result in results], "results": results}
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    return dict(content, snapshot_id="snapshot-" + sha, content_sha256=sha)


def make_manifest(results, mode="synthetic", linkage="synthetic_linked"):
    selected = {}
    for result in results:
        selected[result["module_id"]] = {"module_id": result["module_id"], "module_version": result["module_version"],
                                        "input_artifact_ids": [ref["artifact_id"] for ref in result["artifacts"] if ref["role"] == "source"], "parameters": {}}
    return default_manifest(EPISODE, list(selected.values()), mode=mode, linkage=linkage)


def make_mri(store, job_id="mri-one", origin="synthetic_demo"):
    ref = store.add_bytes(canonical_bytes({"origin": origin, "value": 75}), "application/json")
    request = {"schema_version": "1.0", "job_id": job_id, "episode_id": EPISODE,
               "module_id": "mri.anatomy", "module_version": "0.1.0", "input_artifacts": [ref], "parameters": {}, "protocol_id": None}
    observation = {"observation_id": observation_id_for(job_id, "spine.canal.cross_sectional_area"),
                   "concept": "spine.canal.cross_sectional_area", "assessment_status": "assessed", "value": 75, "unit": "mm2",
                   "anatomy": {"region": "lumbar_spine", "side": "midline", "level": "L4-L5", "numbering_status": "needs_confirmation"},
                   "quality": "usable", "origin": origin, "method": "fixture-geometry", "acquired_at": "2026-10-09T09:55:00+03:00", "coordinate_frame": None,
                   "evidence_refs": [{"artifact_id": ref["artifact_id"], "selector": {"kind": "json", "pointer": "/value"}}], "model": None}
    return result_envelope(request, [observation], [ref])


def make_clinical(store, mri=None, job_id="exam-one", radiculopathy=True, concordance=True,
                  study="performed_result_available", origin="synthetic_demo"):
    status = "assessed" if concordance is not None else "not_assessed"
    if study == "not_performed":
        status, concordance = "not_applicable", None
    source = {"schema_version": "1.0", "source_kind": "clinical-exam.v1",
              "mode": "synthetic_demo" if origin == "synthetic_demo" else "clinician_input", "origin": origin,
              "acquired_at": "2026-10-09T10:05:00+03:00", "assessment_ref": job_id + "-assessment",
              "author": {"id": "fixture-clinician", "role": "clinician"},
              "facts": {WARNING: {"assessment_status": "assessed", "value": True},
                        RADIC: {"assessment_status": "assessed", "value": radiculopathy},
                        STUDY: {"assessment_status": "assessed" if study is not None else "not_assessed", "value": study},
                        CONCORDANCE: {"assessment_status": status, "value": concordance,
                                      "reviewed_result_id": mri["result_id"] if concordance is not None and mri is not None else None}}}
    refs = [store.add_bytes(canonical_bytes(source), "application/json")]
    if mri is not None:
        refs.append(store.add_bytes(canonical_bytes(mri), "application/json", role="derived"))
    request = {"schema_version": "1.0", "job_id": job_id, "episode_id": EPISODE, "module_id": "clinical.exam", "module_version": "0.1.0",
               "input_artifacts": refs, "parameters": {}, "protocol_id": None}
    return ClinicalExamProvider().analyze(request, ProviderContext(store))


class ReadOnlyCatalog:
    def __init__(self, store, refs):
        self.store, self.refs = store, {ref["artifact_id"]: ref for ref in refs}

    def open(self, ref):
        return self.store.open(ref)

    def get_artifact(self, episode_id, artifact_id):
        if episode_id != EPISODE:
            raise ValueError("Wrong scope")
        return self.refs[artifact_id]


def make_attestation(store, results, **changes):
    refs = {ref["artifact_id"]: ref for result in results for ref in result["artifacts"] if ref["role"] == "source"}
    value = {"schema_version": "1.0", "source_kind": "linkage-attestation.v1", "episode_id": EPISODE,
             "subject_ref": "fixture-subject", "session_ref": "fixture-session", "assessment_ref": "binding-one",
             "author": {"id": "operator-one", "role": "research_operator"},
             "sources": [{"artifact_id": ref["artifact_id"], "sha256": ref["sha256"]} for ref in refs.values()]}
    value.update(changes)
    return store.add_bytes(canonical_bytes(value), "application/json")


class SnapshotAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))

    def case_for(self, results, manifest=None, artifacts=None):
        return build_case(make_snapshot(results), manifest or make_manifest(results), artifacts or self.store)

    def test_supported_negative_and_missing_clinical_branches_preserve_false(self):
        mri = make_mri(self.store)
        for radic, concordance, expected in ((True, True, "met"), (False, True, "not_met"), (True, False, "not_met"), (True, None, "indeterminate")):
            exam = make_clinical(self.store, mri, radiculopathy=radic, concordance=concordance)
            case, lineage, warnings = self.case_for([mri, exam])
            self.assertEqual(evaluate_case(case)["results"][0]["status"], expected)
            self.assertIs(case["facts"][RADIC]["value"], radic)
            self.assertIs(case["facts"][CONCORDANCE]["value"], concordance)
            self.assertEqual(lineage[WARNING]["accepted"]["author"]["role"], "clinician")
            self.assertLessEqual(len(case["case_id"]), 64)

    def test_not_performed_is_explicit_and_conflicts_with_any_selected_mri(self):
        exam = make_clinical(self.store, study="not_performed")
        case, lineage, _ = self.case_for([exam])
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "met")
        self.assertEqual(lineage[CONCORDANCE]["reason_codes"], ["not_applicable"])
        mri = make_mri(self.store)
        case, lineage, _ = self.case_for([exam, mri])
        self.assertIsNone(case["facts"][STUDY]["value"])
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "indeterminate")
        self.assertIn("not_performed_contradicts_selected_mri", lineage[STUDY]["reason_codes"])

    def test_missing_exam_and_wrong_producer_do_not_become_clinical_facts(self):
        mri = make_mri(self.store)
        case, _, _ = self.case_for([mri])
        self.assertTrue(all(fact["value"] is None for fact in case["facts"].values()))
        forged = deepcopy(mri)
        forged["observations"][0].update(concept=WARNING, value=True, unit=None, origin="patient_report")
        case, lineage, _ = self.case_for([forged])
        self.assertIsNone(case["facts"][WARNING]["value"])
        self.assertIn("unsupported_clinical_producer", lineage[WARNING]["reason_codes"])

    def test_unlinked_or_mixed_origin_sources_never_support_hypothesis(self):
        real_mri = make_mri(self.store, origin="automatically_measured")
        exam = make_clinical(self.store, real_mri)
        results = [real_mri, exam]
        for mode, linkage in (("synthetic", "synthetic_linked"), ("replay", "unlinked"), ("replay", "unknown")):
            case, lineage, _ = self.case_for(results, make_manifest(results, mode, linkage))
            self.assertTrue(all(fact["value"] is None for fact in case["facts"].values()))
            self.assertEqual(evaluate_case(case)["results"][0]["status"], "indeterminate")
            self.assertIn("mixed_synthetic_real_origins", lineage[WARNING]["reason_codes"])

    def test_real_linkage_needs_readable_attestation_and_exact_source_hashes(self):
        mri = make_mri(self.store, origin="automatically_measured")
        exam = make_clinical(self.store, mri, origin="clinician_entered")
        results = [mri, exam]
        manifest = make_manifest(results, "replay", "unlinked")
        ref = make_attestation(self.store, results)
        manifest.update(linkage="verified_linked", linkage_evidence_artifact_id=ref["artifact_id"])
        case, _, _ = self.case_for(results, manifest)
        self.assertTrue(all(fact["value"] is None for fact in case["facts"].values()))
        case, _, _ = self.case_for(results, manifest, ReadOnlyCatalog(self.store, [ref]))
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "met")
        source = {"artifact_id": mri["artifacts"][0]["artifact_id"], "sha256": "0" * 64}
        bad = make_attestation(self.store, results, sources=[source])
        manifest["linkage_evidence_artifact_id"] = bad["artifact_id"]
        case, _, _ = self.case_for(results, manifest, ReadOnlyCatalog(self.store, [bad]))
        self.assertTrue(all(fact["value"] is None for fact in case["facts"].values()))

    def test_conflicting_assessments_require_explicit_unambiguous_selection(self):
        mri = make_mri(self.store)
        one, two = make_clinical(self.store, mri), make_clinical(self.store, mri, job_id="exam-two", radiculopathy=False)
        results = [mri, one, two]
        manifest = make_manifest(results)
        case, lineage, _ = self.case_for(results, manifest)
        self.assertIsNone(case["facts"][RADIC]["value"])
        self.assertIn("clinical_assessment_conflict_requires_selection", lineage[RADIC]["reason_codes"])
        for observation in one["observations"]:
            manifest["clinical_selection"][observation["concept"]] = observation["observation_id"]
        case, _, _ = self.case_for(results, manifest)
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "met")
        manifest["clinical_selection"][RADIC] = next(obs["observation_id"] for obs in two["observations"] if obs["concept"] == RADIC)
        case, _, _ = self.case_for(results, manifest)
        self.assertIs(case["facts"][RADIC]["value"], False)
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "not_met")

    def test_review_id_must_resolve_to_selected_exact_mri_content(self):
        one, other = make_mri(self.store), make_mri(self.store, job_id="other-mri")
        exam = make_clinical(self.store, one)
        case, lineage, _ = self.case_for([other, exam])
        self.assertIsNone(case["facts"][CONCORDANCE]["value"])
        self.assertIn("reviewed_mri_result_not_selected_or_available", lineage[CONCORDANCE]["reason_codes"])
        modified = deepcopy(one)
        modified["observations"][0]["value"] = 80
        case, lineage, _ = self.case_for([modified, exam])
        self.assertIsNone(case["facts"][CONCORDANCE]["value"])
        self.assertIn("exact_reviewed_mri_artifact_missing", lineage[CONCORDANCE]["reason_codes"])

    def test_known_source_mismatch_or_corruption_stays_unknown(self):
        mri = make_mri(self.store)
        exam = make_clinical(self.store, mri)
        observation = next(obs for obs in exam["observations"] if obs["concept"] == RADIC)
        observation["value"] = False
        case, lineage, _ = self.case_for([mri, exam])
        self.assertIsNone(case["facts"][RADIC]["value"])
        self.assertIn("observation_source_mismatch", lineage[RADIC]["reason_codes"])
        source_ref = exam["artifacts"][0]
        (self.store.root / source_ref["storage_key"]).write_bytes(b"corrupted")
        case, lineage, _ = self.case_for([mri, exam])
        self.assertTrue(all(fact["value"] is None for fact in case["facts"].values()))
        self.assertIn("clinical_source_invalid_or_unavailable", lineage[WARNING]["reason_codes"])

    def test_snapshot_integrity_scope_and_unselected_late_data(self):
        mri = make_mri(self.store)
        exam = make_clinical(self.store, mri)
        snapshot, manifest = make_snapshot([mri, exam]), make_manifest([mri, exam])
        original = deepcopy(snapshot)
        build_case(snapshot, manifest, self.store)
        self.assertEqual(snapshot, original)
        snapshot["results"][0]["warnings"].append({"code": "mutated", "message": "changed"})
        with self.assertRaises(ContractError):
            build_case(snapshot, manifest, self.store)
        with self.assertRaises(ContractError):
            build_case(original, dict(manifest, episode_id="foreign"), self.store)
        unselected = deepcopy(exam)
        unselected.update(result_id="other-result", job_id="other-job", module_id="unselected.module")
        case, _, _ = build_case(make_snapshot([mri, exam, unselected]), manifest, self.store)
        self.assertEqual(evaluate_case(case)["results"][0]["status"], "met")


if __name__ == "__main__":
    unittest.main()
