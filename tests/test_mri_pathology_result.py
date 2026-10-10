"""Software contract checks for categories, provenance and exact evidence."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from dorsopathy_modules.mri_pathology import build_pathology_result
from dorsopathy_platform.contracts import ContractError, validate_result


LEVELS = ("L1-L2", "L2-L3", "L3-L4", "L4-L5", "L5-S1")
CCS = "spine.central_stenosis.grade"
HERNIATION = "spine.disc.herniation.present"


def artifact(identity, media_type="image/png", role="derived", digest="a" * 64):
    return {"artifact_id": identity, "storage_key": "fixtures/" + identity, "sha256": digest,
            "media_type": media_type, "size_bytes": 17, "role": role}


class MRIPathologyResultTests(unittest.TestCase):
    def setUp(self):
        self.source = artifact("input-volume", "application/x-metaimage", "source", "b" * 64)
        self.sidecar = artifact("spinenet-sidecar", "application/json")
        self.artifacts = [self.sidecar]
        self.request = {"schema_version": "1.0", "job_id": "pathology-test", "episode_id": "test-episode",
                        "module_id": "mri.pathology", "module_version": "0.1.0",
                        "input_artifacts": [self.source], "parameters": {"processing_mode": "live"}, "protocol_id": None}
        self.payload = {"schema_version": "mri.spinenet.v1", "processing": "fresh",
                        "source": {"artifact_id": self.source["artifact_id"], "sha256": self.source["sha256"]},
                        "model": {"id": "spinenet-v2-grading", "version": "5e96ac54", "weights_sha256": "c" * 64},
                        "levels": [], "acquired_at": None,
                        "provenance": {"acquisition": "synthetic", "weights_verified": True}}
        # All test inputs are synthetic. This tests the adapter's semantics,
        # not model inference, image interpretation or clinical validity.
        for index, level in enumerate(LEVELS):
            source_png = artifact("context-" + level)
            ivd_png = artifact("ivd-" + level)
            tensor = artifact("tensor-" + level, "application/x-npy")
            self.artifacts.extend([source_png, ivd_png, tensor])
            self.payload["levels"].append({"level": level,
                "grades": {"CentralCanalStenosis": 1, "Herniation": 0},
                "scores": {"CentralCanalStenosis": [.7, .1, .1, .1], "Herniation": [.8, .2]},
                "evidence": {"source_frame_index": index + 11, "source_png_artifact_id": source_png["artifact_id"],
                             "ivd_png_artifact_id": ivd_png["artifact_id"], "model_input_artifact_id": tensor["artifact_id"]}})

    def build(self):
        return build_pathology_result(self.payload, self.request, self.sidecar, self.artifacts)

    def observation(self, result, level, concept):
        return next(item for item in result["observations"] if item["anatomy"]["level"] == level and item["concept"] == concept)

    def test_positive_herniation_and_nonzero_stenosis_preserve_exact_evidence(self):
        level = self.payload["levels"][3]
        level["grades"] = {"CentralCanalStenosis": 3, "Herniation": 1}
        level["scores"] = {"CentralCanalStenosis": [.05, .15, .7, .1], "Herniation": [.1, .9]}
        result = self.build()
        self.assertEqual(result["status"], "partial")
        stenosis = self.observation(result, "L4-L5", CCS)
        herniation = self.observation(result, "L4-L5", HERNIATION)
        self.assertEqual(stenosis["value"], "moderate")
        self.assertIs(herniation["value"], True)
        self.assertEqual(stenosis["anatomy"]["side"], "midline")
        self.assertEqual(herniation["anatomy"]["side"], "unspecified")
        refs = stenosis["evidence_refs"]
        self.assertEqual(refs[0]["artifact_id"], self.source["artifact_id"])
        self.assertEqual(refs[1], {"artifact_id": self.sidecar["artifact_id"], "selector": {
            "kind": "json", "pointer": "/levels/3/grades/CentralCanalStenosis"}})
        self.assertEqual(refs[2]["artifact_id"], level["evidence"]["source_png_artifact_id"])
        self.assertEqual(refs[2]["selector"]["frame_index"], 14)
        self.assertEqual(refs[2]["selector"]["content"], "vertebral_context")
        self.assertEqual(refs[3]["artifact_id"], level["evidence"]["ivd_png_artifact_id"])
        self.assertEqual(refs[4]["artifact_id"], level["evidence"]["model_input_artifact_id"])
        self.assertEqual(herniation["evidence_refs"][1]["selector"]["pointer"], "/levels/3/grades/Herniation")
        # A result remains valid when exported without its request document.
        self.assertEqual(validate_result(result), result)

    def test_explicit_absence_is_assessed_false_and_missing_is_null(self):
        self.payload["levels"].pop(2)
        result = self.build()
        negative = self.observation(result, "L1-L2", HERNIATION)
        self.assertEqual(negative["assessment_status"], "assessed")
        self.assertIs(negative["value"], False)
        self.assertEqual(self.observation(result, "L1-L2", CCS)["value"], "none")
        for concept in (CCS, HERNIATION):
            missing = self.observation(result, "L3-L4", concept)
            self.assertEqual(missing["assessment_status"], "not_assessed")
            self.assertIsNone(missing["value"])
            self.assertEqual(len(missing["evidence_refs"]), 2)
        warning = next(item for item in result["warnings"] if item["code"] == "level_not_assessed")
        self.assertIn("L3-L4", warning["message"])
        self.assertIn("not detected", warning["message"])
        # Raw sidecar positions, not sorted output positions, resolve evidence.
        pointer = self.observation(result, "L4-L5", CCS)["evidence_refs"][1]["selector"]["pointer"]
        self.assertEqual(pointer, "/levels/2/grades/CentralCanalStenosis")

    def test_no_detected_levels_never_becomes_a_normal_study(self):
        self.payload["levels"] = []
        result = self.build()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 10)
        self.assertTrue(all(item["assessment_status"] == "not_assessed" and item["value"] is None for item in result["observations"]))
        self.assertEqual(sum(item["code"] == "level_not_assessed" for item in result["warnings"]), 5)

    def test_category_boundaries_and_ties_match_first_argmax(self):
        self.payload["levels"][0]["grades"]["CentralCanalStenosis"] = 4
        self.payload["levels"][0]["scores"]["CentralCanalStenosis"] = [0, 0, 0, 1]
        self.payload["levels"][1]["grades"]["CentralCanalStenosis"] = 2
        self.payload["levels"][1]["scores"]["CentralCanalStenosis"] = [0, 1, 0, 0]
        self.payload["levels"][2]["scores"] = {"CentralCanalStenosis": [.25] * 4, "Herniation": [.5, .5]}
        result = self.build()
        self.assertEqual(self.observation(result, "L1-L2", CCS)["value"], "severe")
        self.assertEqual(self.observation(result, "L2-L3", CCS)["value"], "mild")
        self.assertEqual(self.observation(result, "L3-L4", CCS)["value"], "none")
        self.assertIs(self.observation(result, "L3-L4", HERNIATION)["value"], False)
        self.payload["levels"][2]["grades"]["Herniation"] = 1
        with self.assertRaises(ContractError):
            self.build()

    def test_metadata_remains_unknown_partial_and_non_diagnostic(self):
        result = self.build()
        self.assertEqual(len({item["observation_id"] for item in result["observations"]}), 10)
        self.assertEqual({item["concept"] for item in result["observations"]}, {CCS, HERNIATION})
        for item in result["observations"]:
            self.assertEqual(item["quality"], "unknown")
            self.assertIsNone(item["unit"])
            self.assertIsNone(item["acquired_at"])
            self.assertEqual(item["anatomy"]["region"], "lumbar_spine")
            self.assertEqual(item["anatomy"]["numbering_status"], "needs_confirmation")
            self.assertEqual(item["coordinate_frame"], "SpineNet-canonical-sagittal-pixels")
            self.assertEqual(item["model"], self.payload["model"])
            self.assertNotIn("scores", item)
            self.assertNotIn("probability", item)
        codes = {item["code"] for item in result["warnings"]}
        self.assertTrue({"model_category_not_diagnosis", "unsupported_task_coverage", "numbering_needs_confirmation", "context_image_not_pathology_contour"} <= codes)

    def test_synthetic_fresh_inference_cannot_masquerade_as_recorded(self):
        result = self.build()
        self.assertEqual(self.payload["processing"], "fresh")
        self.assertTrue(all(item["origin"] == "synthetic_demo" for item in result["observations"]))
        self.assertIn("synthetic_input", {item["code"] for item in result["warnings"]})
        # Test only the recorded metadata branch, never image interpretation.
        self.payload["provenance"]["acquisition"] = "recorded"
        recorded = self.build()
        self.assertTrue(all(item["origin"] == "model_predicted" for item in recorded["observations"]))
        del self.payload["provenance"]["acquisition"]
        with self.assertRaises(ContractError):
            self.build()

    def test_source_acquisition_time_is_preserved_or_rejected_without_timezone(self):
        self.payload["acquired_at"] = "2026-10-10T12:00:00+03:00"
        self.assertTrue(all(item["acquired_at"] == self.payload["acquired_at"] for item in self.build()["observations"]))
        self.payload["acquired_at"] = "2026-10-10T12:00:00"
        with self.assertRaises(ContractError):
            self.build()

    def test_duplicates_unsupported_levels_and_unexpected_fields_reject(self):
        pristine = copy.deepcopy(self.payload)
        self.payload["levels"].append(copy.deepcopy(self.payload["levels"][0]))
        with self.assertRaises(ContractError):
            self.build()
        self.payload = copy.deepcopy(pristine)
        self.payload["levels"][0]["level"] = "T12-L1"
        with self.assertRaises(ContractError):
            self.build()
        self.payload = copy.deepcopy(pristine)
        self.payload["levels"][0]["diagnosis"] = "unvalidated"
        with self.assertRaises(ContractError):
            self.build()
        self.payload = copy.deepcopy(pristine)
        self.payload["processing"] = "replay"
        with self.assertRaises(ContractError):
            self.build()
        self.payload = copy.deepcopy(pristine)
        self.payload["schema_version"] = "mri.spinenet.v2"
        with self.assertRaises(ContractError):
            self.build()

    def test_boolean_and_out_of_range_class_codes_reject(self):
        for head, values in (("CentralCanalStenosis", (True, 0, 5, 1.0, "1")), ("Herniation", (False, -1, 2, 0.0, "0"))):
            original = self.payload["levels"][0]["grades"][head]
            for value in values:
                with self.subTest(head=head, value=value):
                    self.payload["levels"][0]["grades"][head] = value
                    with self.assertRaises(ContractError):
                        self.build()
            self.payload["levels"][0]["grades"][head] = original

    def test_class_scores_reject_bad_count_bounds_finiteness_sum_and_class_mismatch(self):
        invalid = ([.7, .1, .2], [1.1, -.1, 0, 0], [float("nan"), 0, 0, 1],
                   [float("inf"), 0, 0, 0], [.6, .1, .1, .1], [True, 0, 0, 0],
                   ["0.7", .1, .1, .1], [.1, .7, .1, .1])
        for scores in invalid:
            with self.subTest(scores=scores):
                self.payload["levels"][0]["scores"]["CentralCanalStenosis"] = scores
                with self.assertRaises(ContractError):
                    self.build()
        self.payload["levels"][0]["scores"]["CentralCanalStenosis"] = [.7, .1, .1, .1]
        self.payload["levels"][0]["scores"]["Herniation"] = [.1, .9]
        with self.assertRaises(ContractError):
            self.build()

    def test_model_hash_and_identity_are_mandatory_even_without_levels(self):
        self.payload["levels"] = []
        for value in (None, "", "d" * 63, "g" * 64, True):
            with self.subTest(hash=value):
                self.payload["model"]["weights_sha256"] = value
                with self.assertRaises(ContractError):
                    self.build()
        self.payload["model"]["weights_sha256"] = "c" * 64
        self.payload["model"]["id"] = "unverified-other-model"
        with self.assertRaises(ContractError):
            self.build()

    def test_source_id_and_sha_must_match_exact_request_input(self):
        for field, replacement in (("artifact_id", "other-source"), ("sha256", "d" * 64)):
            original = self.payload["source"][field]
            with self.subTest(field=field):
                self.payload["source"][field] = replacement
                with self.assertRaises(ContractError):
                    self.build()
            self.payload["source"][field] = original
        conflicting = copy.deepcopy(self.source)
        conflicting["sha256"] = "d" * 64
        self.artifacts.append(conflicting)
        with self.assertRaises(ContractError):
            self.build()

    def test_all_evidence_and_sidecar_refs_must_be_declared(self):
        links = self.payload["levels"][0]["evidence"]
        for field in ("source_png_artifact_id", "ivd_png_artifact_id", "model_input_artifact_id"):
            original = links[field]
            with self.subTest(field=field):
                links[field] = "undeclared-artifact"
                with self.assertRaises(ContractError):
                    self.build()
            links[field] = original
        self.artifacts.remove(self.sidecar)
        with self.assertRaises(ContractError):
            self.build()

    def test_wrong_evidence_artifact_type_frame_or_duplicate_ref_rejects(self):
        pristine = copy.deepcopy(self.artifacts)
        self.artifacts[1]["media_type"] = "application/json"
        with self.assertRaises(ContractError):
            self.build()
        self.artifacts = copy.deepcopy(pristine)
        self.artifacts[1]["role"] = "source"
        with self.assertRaises(ContractError):
            self.build()
        self.artifacts = copy.deepcopy(pristine)
        for value in (-1, True, 1.0, None):
            with self.subTest(frame=value):
                self.payload["levels"][0]["evidence"]["source_frame_index"] = value
                with self.assertRaises(ContractError):
                    self.build()
        self.payload["levels"][0]["evidence"]["source_frame_index"] = 11
        self.artifacts.append(copy.deepcopy(self.artifacts[1]))
        with self.assertRaises(ContractError):
            self.build()

    def test_wrong_request_module_version_parameters_and_input_count_reject(self):
        original = copy.deepcopy(self.request)
        for field, value in (("module_id", "mri.anatomy"), ("module_version", "0.2.0"),
                             ("parameters", {}), ("parameters", {"processing_mode": "replay"}),
                             ("input_artifacts", []), ("input_artifacts", [self.source, artifact("second-source", role="source")])):
            with self.subTest(field=field, value=value):
                self.request = copy.deepcopy(original)
                self.request[field] = value
                with self.assertRaises(ContractError):
                    self.build()

    def test_sidecar_requires_complete_matching_derived_json_reference(self):
        original = copy.deepcopy(self.sidecar)
        self.sidecar["sha256"] = "d" * 64
        # Mutating the independently supplied sidecar must not mutate its
        # declared output reference or silently rebind the evidence identity.
        self.artifacts[0] = original
        with self.assertRaises(ContractError):
            self.build()
        self.sidecar = copy.deepcopy(original)
        self.artifacts[0] = copy.deepcopy(original)
        self.artifacts[0]["role"] = "source"
        with self.assertRaises(ContractError):
            self.build()
        self.artifacts[0] = copy.deepcopy(original)
        del self.sidecar["storage_key"]
        with self.assertRaises(ContractError):
            self.build()

    def test_provenance_rejects_non_json_nonfinite_and_cyclic_values(self):
        for value in (float("nan"), (1, 2), {1: "non-string-key"}, {"not-json"}):
            with self.subTest(value_type=type(value).__name__):
                self.payload["provenance"]["audit"] = value
                with self.assertRaises(ContractError):
                    self.build()
        del self.payload["provenance"]["audit"]
        self.payload["provenance"]["cycle"] = self.payload["provenance"]
        with self.assertRaises(ContractError):
            self.build()

    def test_missing_and_unsupported_heads_cannot_assert_other_task_coverage(self):
        original = copy.deepcopy(self.payload["levels"][0])
        del self.payload["levels"][0]["scores"]["Herniation"]
        with self.assertRaises(ContractError):
            self.build()
        self.payload["levels"][0] = copy.deepcopy(original)
        self.payload["levels"][0]["grades"]["ForaminalStenosis"] = 0
        with self.assertRaises(ContractError):
            self.build()

    def test_returns_independent_copy_without_mutating_payload_request_or_artifacts(self):
        before = copy.deepcopy((self.payload, self.request, self.artifacts))
        result = self.build()
        self.assertEqual((self.payload, self.request, self.artifacts), before)
        result["observations"][0]["model"]["weights_sha256"] = "d" * 64
        result["artifacts"][0]["size_bytes"] = 999
        self.assertEqual((self.payload, self.request, self.artifacts), before)

    def test_import_and_result_build_need_no_heavy_imaging_or_model_dependencies(self):
        fixture = json.dumps([self.payload, self.request, self.sidecar, self.artifacts])
        script = """
import builtins
import json
import sys
blocked = {'numpy', 'torch', 'SimpleITK', 'pydicom', 'spinenet', 'PIL'}
original = builtins.__import__
def light_import(name, *args, **kwargs):
    if name.split('.')[0] in blocked:
        raise AssertionError('heavy import attempted')
    return original(name, *args, **kwargs)
builtins.__import__ = light_import
from dorsopathy_modules.mri_pathology import build_pathology_result
result = build_pathology_result(*json.loads(sys.argv[1]))
assert result['status'] == 'partial'
assert all(item['origin'] == 'synthetic_demo' for item in result['observations'])
assert not (blocked & set(sys.modules))
"""
        completed = subprocess.run([sys.executable, "-c", script, fixture], cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
