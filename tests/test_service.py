from copy import deepcopy
import json
from pathlib import Path
import unittest

from dorsopathy_cds.service import PayloadError, evaluate_case, load_catalog

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def case(filename="01_supported_with_imaging.json"):
    return json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))


class ClinicalServiceTests(unittest.TestCase):
    def test_catalog_has_anchored_quotes_for_every_leaf(self):
        rules, sources = load_catalog()
        result = evaluate_case(case())["results"][0]
        self.assertEqual(len(rules), 1)
        self.assertEqual(len(result["citations"]), 3)
        self.assertTrue(all(leaf["citation_refs"] for leaf in result["leaves"]))
        self.assertEqual(sources["documents"][0]["guideline_version"], 1)
        report = evaluate_case(case())
        source = report["source_documents"][0]
        self.assertEqual(source["id"], "777_1")
        self.assertTrue(source["pdf_url"].startswith("https://apicr.minzdrav.gov.ru/"))
        self.assertEqual(len(source["pdf_sha256"]), 64)

    def test_demonstration_cases(self):
        expected = {
            "01_supported_with_imaging.json": "met",
            "02_unknown_concordance.json": "indeterminate",
            "03_discordant_imaging.json": "not_met",
            "04_no_imaging_performed.json": "met",
            "05_warning_signs_not_absent.json": "not_met",
        }
        for filename, status in expected.items():
            with self.subTest(filename=filename):
                result = evaluate_case(case(filename))["results"][0]
                self.assertEqual(result["status"], status)
                self.assertEqual(result["clinical_review_status"], "requires_clinical_review")

    def test_unperformed_imaging_is_not_imaging_confirmation(self):
        result = evaluate_case(case("04_no_imaging_performed.json"))["results"][0]
        self.assertEqual(result["imaging_concordance_status"], "not_applicable")
        self.assertEqual(result["decisive_missing_fact_ids"], [])
        self.assertIn("clinical.imaging_clinical_concordance", result["missing_fact_ids"])

    def test_missing_upload_does_not_mean_unperformed(self):
        payload = case("04_no_imaging_performed.json")
        del payload["facts"]["imaging.study_status"]
        result = evaluate_case(payload)["results"][0]
        self.assertEqual(result["status"], "indeterminate")
        self.assertIn("imaging.study_status", result["decisive_missing_fact_ids"])

    def test_unknown_and_false_are_different(self):
        payload = case()
        payload["facts"]["clinical.radiculopathy_signs_and_positive_tension_tests"]["value"] = None
        self.assertEqual(evaluate_case(payload)["results"][0]["status"], "indeterminate")
        payload["facts"]["clinical.radiculopathy_signs_and_positive_tension_tests"]["value"] = False
        self.assertEqual(evaluate_case(payload)["results"][0]["status"], "not_met")

    def test_inapplicable_known_concordance_rejected(self):
        for value in (True, False):
            payload = case("04_no_imaging_performed.json")
            payload["facts"]["clinical.imaging_clinical_concordance"] = {"value": value, "origin": "synthetic_demo", "assessment_ref": "demo"}
            with self.assertRaises(PayloadError):
                evaluate_case(payload)

    def test_known_concordance_requires_available_result(self):
        payload = case()
        payload["facts"]["imaging.study_status"]["value"] = None
        with self.assertRaises(PayloadError):
            evaluate_case(payload)

    def test_bad_values_and_extra_fields_rejected(self):
        for value in (1, "true", [], {}):
            payload = case()
            payload["facts"]["clinical.specific_cause_warning_signs_absent"]["value"] = value
            with self.assertRaises(PayloadError):
                evaluate_case(payload)
        for field in ("patient_name", "probability"):
            payload = case()
            payload[field] = "not-accepted"
            with self.assertRaises(PayloadError):
                evaluate_case(payload)

    def test_unsupported_fact_and_enum_rejected(self):
        payload = case()
        payload["facts"]["imaging.study_status"]["value"] = "unknown"
        with self.assertRaises(PayloadError):
            evaluate_case(payload)
        payload = case()
        payload["facts"]["sensor.pain_confirmed"] = {"value": True}
        with self.assertRaises(PayloadError):
            evaluate_case(payload)

    def test_known_assessment_requires_provenance(self):
        for field in ("origin", "assessment_ref"):
            payload = case()
            del payload["facts"]["clinical.specific_cause_warning_signs_absent"][field]
            with self.assertRaises(PayloadError):
                evaluate_case(payload)
        payload = case()
        payload["facts"]["clinical.specific_cause_warning_signs_absent"]["origin"] = "mri_model"
        with self.assertRaises(PayloadError):
            evaluate_case(payload)

    def test_scope_must_be_adult(self):
        for population in (None, "child", 18):
            payload = case()
            payload["population"] = population
            with self.assertRaises(PayloadError):
                evaluate_case(payload)

    def test_catalog_hash_and_determinism(self):
        payload = case()
        before = deepcopy(payload)
        first = evaluate_case(payload)
        self.assertEqual(len(first["catalog_sha256"]), 64)
        self.assertEqual(first, evaluate_case(payload))
        self.assertEqual(before, payload)
        self.assertTrue(first["synthetic_demo"])
        self.assertEqual(first["mode"], "research_only")

    def test_clinician_entered_provenance(self):
        payload = case()
        for entry in payload["facts"].values():
            entry["origin"] = "clinician_entered"
        report = evaluate_case(payload)
        self.assertFalse(report["synthetic_demo"])
        self.assertEqual(report["facts_provenance"]["clinical.imaging_clinical_concordance"]["assessment_ref"], "demo-imaging-clinical-review-1")

    def test_empty_assessments_are_unknown(self):
        result = evaluate_case({"case_id": "empty", "population": "adult", "facts": {}})["results"][0]
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(len(result["missing_fact_ids"]), 4)


if __name__ == "__main__":
    unittest.main()
