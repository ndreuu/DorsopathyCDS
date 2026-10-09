import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dorsopathy_modules.complaints import ComplaintsProvider
from dorsopathy_platform.contracts import ContractError, loads_json, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "complaints"


class ComplaintsProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))
        self.provider = ComplaintsProvider()
        self.source = json.loads((EXAMPLES / "source_known_zero.json").read_text())

    def analyze(self, source=None, **request_updates):
        ref = self.store.add_bytes(json.dumps(self.source if source is None else source).encode(), "application/json")
        request = {"schema_version": "1.0", "job_id": "complaints-test-job", "episode_id": "test-episode",
                   "module_id": "complaints", "module_version": "0.1.0", "input_artifacts": [ref],
                   "parameters": {}, "protocol_id": None}
        request.update(request_updates)
        return self.provider.analyze(request, ProviderContext(self.store)), ref

    def test_zero_time_evidence_and_body_map_are_preserved(self):
        result, source_ref = self.analyze()
        validate_result(result)
        intensity, duration = result["observations"]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(intensity["value"], 0)
        self.assertEqual(duration["value"], 14)
        self.assertEqual(intensity["origin"], "synthetic_demo")
        self.assertEqual(intensity["acquired_at"], self.source["acquired_at"])
        self.assertIsNone(intensity["anatomy"]["level"])
        self.assertEqual(intensity["evidence_refs"], [{"artifact_id": source_ref["artifact_id"],
                         "selector": {"kind": "json", "pointer": "/answers/pain_intensity"}}])
        derived = [ref for ref in result["artifacts"] if ref["role"] == "derived"]
        self.assertEqual(len(derived), 1)
        bodymap = loads_json(self.store.read(derived[0]))
        self.assertEqual(bodymap["bodymap"]["selected_regions"], ["lower_back"])
        self.assertEqual(bodymap["source_artifact_id"], source_ref["artifact_id"])
        self.assertEqual(bodymap["author"], self.source["author"])

    def test_unknown_answers_remain_null_and_partial(self):
        source = json.loads((EXAMPLES / "source_unknown.json").read_text())
        result, _ = self.analyze(source)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["observations"][0]["value"])
        self.assertEqual(result["observations"][0]["assessment_status"], "not_assessed")
        derived = result["artifacts"][1]
        self.assertIsNone(loads_json(self.store.read(derived))["bodymap"]["selected_regions"])

    def test_answered_empty_map_is_distinct_from_unknown(self):
        self.source["bodymap"]["selected_regions"] = []
        result, _ = self.analyze()
        self.assertEqual(result["status"], "complete")
        bodymap = loads_json(self.store.read(result["artifacts"][1]))
        self.assertEqual(bodymap["bodymap"], {"assessment_status": "assessed", "selected_regions": []})

    def test_real_patient_input_preserves_patient_origin(self):
        self.source.update(mode="patient_input", origin="patient_report", acquired_at=None)
        result, _ = self.analyze()
        self.assertEqual({o["origin"] for o in result["observations"]}, {"patient_report"})
        self.assertIsNone(result["observations"][0]["acquired_at"])

    def test_not_applicable_is_distinct_from_not_assessed(self):
        self.source["answers"]["duration"].update(assessment_status="not_applicable", value=None)
        result, _ = self.analyze()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["observations"][1]["assessment_status"], "not_applicable")

    def test_invalid_intensity_duration_and_status_are_rejected(self):
        for field, values in (("pain_intensity", [False, -1, 11, 2.5, "3"]),
                              ("duration", [True, -1, "14", float("inf")])):
            for value in values:
                with self.subTest(field=field, value=value):
                    source = json.loads(json.dumps(self.source))
                    source["answers"][field]["value"] = value
                    with self.assertRaises(ContractError):
                        self.analyze(source)
        self.source["answers"]["pain_intensity"]["assessment_status"] = "not_assessed"
        with self.assertRaises(ContractError):
            self.analyze()

    def test_invalid_mode_side_time_and_unknown_fields_are_rejected(self):
        mutations = [("mode", "patient_input"), ("mode", []), ("acquired_at", "2026-10-09T10:00:00"),
                     ("unexpected", "not-permitted")]
        for field, value in mutations:
            with self.subTest(field=field):
                source = json.loads(json.dumps(self.source)); source[field] = value
                with self.assertRaises(ContractError):
                    self.analyze(source)
        self.source["anatomy"]["side"] = []
        with self.assertRaises(ContractError):
            self.analyze()

    def test_duplicate_map_zones_and_unassessed_nonnull_map_are_rejected(self):
        self.source["bodymap"]["selected_regions"] = ["lower_back", "lower_back"]
        with self.assertRaises(ContractError):
            self.analyze()
        self.source["bodymap"] = {"assessment_status": "not_assessed", "selected_regions": []}
        with self.assertRaises(ContractError):
            self.analyze()

    def test_provider_context_and_parameters_are_strict(self):
        for update in ({"module_id": "demo.complaints"}, {"module_version": "2.0.0"},
                       {"parameters": {"infer_diagnosis": True}}, {"input_artifacts": []}):
            with self.subTest(update=update), self.assertRaises(ContractError):
                self.analyze(**update)

    def test_shipped_synthetic_request_result_pairs_reproduce(self):
        for name in ("known_zero", "unknown"):
            with self.subTest(name=name):
                request = json.loads((EXAMPLES / ("module_request_" + name + ".json")).read_text())
                ref = self.store.add_bytes((EXAMPLES / ("source_" + name + ".json")).read_bytes(), "application/json")
                self.assertEqual(request["input_artifacts"], [ref])
                actual = self.provider.analyze(request, ProviderContext(self.store))
                expected = json.loads((EXAMPLES / ("module_result_" + name + ".json")).read_text())
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
