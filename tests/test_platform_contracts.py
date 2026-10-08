"""Synthetic wire-contract tests; no clinical rules or patient records."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from dorsopathy_platform.contracts import (
    ContractError, SCHEMA_VERSION, loads_json, validate_request, validate_result,
)


def artifact(identifier="source-1", role="source"):
    return {
        "artifact_id": identifier,
        "storage_key": "synthetic/{}.json".format(identifier),
        "sha256": "a" * 64,
        "media_type": "application/json",
        "size_bytes": 123,
        "role": role,
    }


def request():
    return {
        "schema_version": "1.0",
        "job_id": "job-1",
        "episode_id": "episode-1",
        "module_id": "synthetic.measurement",
        "module_version": "1.0.0",
        "input_artifacts": [artifact()],
        "parameters": {"nested": [False, 0, None, {"label": "synthetic"}]},
        "protocol_id": None,
    }


def observation():
    return {
        "observation_id": "observation-1",
        "concept": "synthetic.distance",
        "assessment_status": "assessed",
        "value": 0.0,
        "unit": "mm",
        "anatomy": {
            "region": "lumbar_spine",
            "side": "unspecified",
            "level": None,
            "numbering_status": "not_applicable",
        },
        "quality": "usable",
        "origin": "synthetic_demo",
        "method": "synthetic fixture",
        "evidence_refs": [{"artifact_id": "source-1", "selector": {"field": "distance"}}],
        "model": None,
        "acquired_at": None,
        "coordinate_frame": None,
    }


def result():
    req = request()
    return {
        "schema_version": "1.0",
        "result_id": "result-1",
        "job_id": req["job_id"],
        "episode_id": req["episode_id"],
        "module_id": req["module_id"],
        "module_version": req["module_version"],
        "status": "complete",
        "observations": [observation()],
        "artifacts": [artifact()],
        "warnings": [],
        "error": None,
    }


class RequestTests(unittest.TestCase):
    def test_valid_request_preserved_and_copied(self):
        payload = request()
        snapshot = deepcopy(payload)
        validated = validate_request(payload)
        self.assertEqual(validated, snapshot)
        validated["parameters"]["nested"].append("modified copy")
        validated["input_artifacts"][0]["size_bytes"] = 0
        self.assertEqual(payload, snapshot)

    def test_request_exact_fields_and_schema_version(self):
        for field in request():
            payload = request()
            del payload[field]
            with self.subTest(missing=field), self.assertRaises(ContractError):
                validate_request(payload)
        payload = request()
        payload["unexpected"] = "do not silently discard"
        with self.assertRaises(ContractError):
            validate_request(payload)
        for version in (1.0, "2.0", None):
            payload = request()
            payload["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ContractError):
                validate_request(payload)

    def test_opaque_identifier_syntax(self):
        for identifier in ("", ".", "..", "a/b", "x y", "идентификатор", "x\n", "a" * 101, 1):
            payload = request()
            payload["job_id"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(ContractError):
                validate_request(payload)
        payload = request()
        payload["module_id"] = "spine.measurement_v1-test"
        payload["protocol_id"] = "lumbar.t2.v1"
        self.assertEqual(validate_request(payload), payload)

    def test_storage_key_rejects_traversal_absolute_paths_and_control_characters(self):
        for key in ("/absolute", "../file", "a/../file", "a/./file", "a//file", "a/", "C:/file", "a\\file", "a\0file", "a\nfile", " "):
            payload = request()
            payload["input_artifacts"][0]["storage_key"] = key
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_request(payload)

    def test_artifact_digest_size_and_role_validation(self):
        for field, value in (
            ("sha256", "a" * 63), ("sha256", "z" * 64),
            ("size_bytes", -1), ("size_bytes", True), ("size_bytes", 1.0),
            ("role", "arbitrary"), ("media_type", ""),
        ):
            payload = request()
            payload["input_artifacts"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                validate_request(payload)
        payload = request()
        payload["input_artifacts"][0]["size_bytes"] = 0
        payload["input_artifacts"][0]["sha256"] = "A" * 64
        validate_request(payload)

    def test_duplicate_input_artifact_identifier_rejected(self):
        payload = request()
        duplicate = artifact()
        duplicate["storage_key"] = "different/file.json"
        payload["input_artifacts"].append(duplicate)
        with self.assertRaises(ContractError):
            validate_request(payload)

    def test_parameters_are_finite_json_object_only(self):
        for parameters in ([], {"x": float("nan")}, {"x": [float("inf")]}, {1: "invalid key"}, {"x": (1, 2)}):
            payload = request()
            payload["parameters"] = parameters
            with self.subTest(parameters=parameters), self.assertRaises(ContractError):
                validate_request(payload)
        payload = request()
        cyclic = []
        cyclic.append(cyclic)
        payload["parameters"] = {"x": cyclic}
        with self.assertRaises(ContractError):
            validate_request(payload)

    def test_parameters_reject_deep_nesting_before_copying(self):
        payload = request()
        nested = None
        for _ in range(1000):
            nested = [nested]
        payload["parameters"] = {"synthetic-private-field": nested}
        with self.assertRaises(ContractError) as error:
            validate_request(payload)
        self.assertNotIn("synthetic-private-field", str(error.exception))

    def test_valid_nested_parameters_remain_independent(self):
        payload = request()
        nested = {"value": 0}
        for _ in range(16):
            nested = [nested]
        payload["parameters"] = {"nested": nested}
        validated = validate_request(payload)
        self.assertEqual(validated, payload)
        self.assertIsNot(validated["parameters"]["nested"], nested)


class ResultTests(unittest.TestCase):
    def test_valid_result_is_independent_copy(self):
        payload = result()
        snapshot = deepcopy(payload)
        validated = validate_result(payload, request())
        self.assertEqual(validated, snapshot)
        validated["observations"][0]["evidence_refs"][0]["selector"]["field"] = "changed"
        validated["artifacts"][0]["sha256"] = "b" * 64
        self.assertEqual(payload, snapshot)

    def test_result_exact_fields_and_context_match(self):
        for field in result():
            payload = result()
            del payload[field]
            with self.subTest(missing=field), self.assertRaises(ContractError):
                validate_result(payload)
        for field in ("job_id", "episode_id", "module_id", "module_version"):
            payload = result()
            payload[field] = "different"
            with self.subTest(context=field), self.assertRaises(ContractError):
                validate_result(payload, request())

    def test_unknown_extra_metadata_is_rejected_at_every_fixed_object(self):
        for target in ("result", "observation", "anatomy", "artifact", "evidence", "model", "warning"):
            payload = result()
            item = payload["observations"][0]
            item["model"] = {"id": "synthetic", "version": "1.0", "weights_sha256": "b" * 64}
            payload["warnings"] = [{"code": "synthetic", "message": "Synthetic warning"}]
            objects = {
                "result": payload, "observation": item, "anatomy": item["anatomy"],
                "artifact": payload["artifacts"][0], "evidence": item["evidence_refs"][0],
                "model": item["model"], "warning": payload["warnings"][0],
            }
            objects[target]["unexpected"] = "must reject"
            with self.subTest(target=target), self.assertRaises(ContractError):
                validate_result(payload)

    def test_assessment_states_preserve_false_zero_unknown_and_not_applicable(self):
        payload = result()
        item = payload["observations"][0]
        item.update(value=False, unit=None, quality="unknown")
        self.assertIs(validate_result(payload)["observations"][0]["value"], False)
        self.assertEqual(validate_result(result())["observations"][0]["value"], 0.0)
        item.update(assessment_status="not_assessed", value=None, evidence_refs=[])
        payload["status"] = "partial"
        self.assertIsNone(validate_result(payload)["observations"][0]["value"])
        item["assessment_status"] = "not_applicable"
        payload["status"] = "complete"
        self.assertEqual(validate_result(payload)["observations"][0]["assessment_status"], "not_applicable")

    def test_assessment_value_mismatch_rejected(self):
        for assessment, value in (("assessed", None), ("not_assessed", False), ("not_applicable", 0)):
            payload = result()
            payload["status"] = "partial"
            payload["observations"][0].update(assessment_status=assessment, value=value, unit=None)
            with self.subTest(assessment=assessment, value=value), self.assertRaises(ContractError):
                validate_result(payload)

    def test_numeric_units_and_finite_scalar_values(self):
        for value, unit in ((1, None), (True, "mm"), ("text", "mm"), (float("nan"), "mm"), (float("inf"), "mm"), ([], None), ({}, None)):
            payload = result()
            payload["observations"][0].update(value=value, unit=unit)
            with self.subTest(value=value, unit=unit), self.assertRaises(ContractError):
                validate_result(payload)
        payload = result()
        payload["observations"][0]["quality"] = "unreliable"
        self.assertEqual(validate_result(payload)["observations"][0]["value"], 0.0)

    def test_complete_cannot_hide_unassessed_observation(self):
        payload = result()
        payload["observations"][0].update(assessment_status="not_assessed", value=None)
        with self.assertRaises(ContractError):
            validate_result(payload)
        payload["status"] = "partial"
        validate_result(payload)

    def test_failed_requires_error_and_no_observations(self):
        payload = result()
        payload["status"] = "failed"
        with self.assertRaises(ContractError):
            validate_result(payload)
        payload["error"] = {"code": "synthetic.failure", "message": "Synthetic failure"}
        with self.assertRaises(ContractError):
            validate_result(payload)
        payload["observations"] = []
        validate_result(payload)
        for status in ("complete", "partial"):
            payload["status"] = status
            with self.subTest(status=status), self.assertRaises(ContractError):
                validate_result(payload)

    def test_assessed_requires_evidence(self):
        payload = result()
        payload["observations"][0]["evidence_refs"] = []
        with self.assertRaises(ContractError):
            validate_result(payload)

    def test_evidence_must_resolve_and_may_use_request_input(self):
        payload = result()
        payload["artifacts"] = []
        with self.assertRaises(ContractError):
            validate_result(payload)
        validate_result(payload, request())
        payload["observations"][0]["evidence_refs"][0]["artifact_id"] = "unknown"
        with self.assertRaises(ContractError):
            validate_result(payload, request())

    def test_conflicting_input_output_artifact_metadata_rejected(self):
        payload = result()
        payload["artifacts"][0]["sha256"] = "b" * 64
        with self.assertRaises(ContractError):
            validate_result(payload, request())

    def test_duplicate_output_artifacts_and_observation_ids_rejected(self):
        payload = result()
        payload["artifacts"].append(deepcopy(payload["artifacts"][0]))
        with self.assertRaises(ContractError):
            validate_result(payload)
        payload = result()
        duplicate = deepcopy(payload["observations"][0])
        duplicate["concept"] = "synthetic.other"
        payload["observations"].append(duplicate)
        with self.assertRaises(ContractError):
            validate_result(payload)

    def test_selector_must_be_finite_json_object(self):
        for selector in ([], {"coordinate": float("nan")}, {1: 2}):
            payload = result()
            payload["observations"][0]["evidence_refs"][0]["selector"] = selector
            with self.subTest(selector=selector), self.assertRaises(ContractError):
                validate_result(payload)

    def test_evidence_selector_rejects_deep_nesting_but_accepts_valid_nested_coordinates(self):
        payload = result()
        nested = [0.0, 1.0]
        for _ in range(1000):
            nested = [nested]
        payload["observations"][0]["evidence_refs"][0]["selector"] = {"coordinates": nested}
        with self.assertRaises(ContractError):
            validate_result(payload)
        valid = result()
        valid["observations"][0]["evidence_refs"][0]["selector"] = {
            "planes": [{"rows": [[0.0, 1.0], [2.0, 3.0]]}], "frame_index": 0,
        }
        self.assertEqual(validate_result(valid), valid)

    def test_model_prediction_requires_model_identity_version_and_weights(self):
        payload = result()
        item = payload["observations"][0]
        item["origin"] = "model_predicted"
        with self.assertRaises(ContractError):
            validate_result(payload)
        item["model"] = {"id": "synthetic.model", "version": "1.0", "weights_sha256": "b" * 64}
        validate_result(payload)
        item["model"]["weights_sha256"] = "untracked"
        with self.assertRaises(ContractError):
            validate_result(payload)

    def test_origin_and_anatomical_statuses_preserved(self):
        payload = result()
        item = payload["observations"][0]
        item["origin"] = "clinician_entered"
        item["anatomy"].update(side="left", level="L4-L5", numbering_status="needs_confirmation")
        validated = validate_result(payload)
        self.assertEqual(validated["observations"][0]["origin"], "clinician_entered")
        self.assertEqual(validated["observations"][0]["anatomy"]["numbering_status"], "needs_confirmation")
        for field, value in (("quality", "approved"), ("origin", "AI"), ("concept", "unqualified")):
            bad = deepcopy(payload)
            bad["observations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_result(bad)

    def test_source_acquisition_time_and_coordinate_frame_are_preserved(self):
        for timestamp in ("2026-10-08T12:34:56Z", "2026-10-08T12:34:56.123+03:00", "2026-10-08T12:34:56-05:30"):
            payload = result()
            item = payload["observations"][0]
            item.update(acquired_at=timestamp, coordinate_frame="DICOM:synthetic-frame-1")
            validated = validate_result(payload)
            self.assertEqual(validated["observations"][0]["acquired_at"], timestamp)
            self.assertEqual(validated["observations"][0]["coordinate_frame"], "DICOM:synthetic-frame-1")

    def test_source_acquisition_time_rejects_naive_date_only_and_nonsense(self):
        for timestamp in ("2026-10-08", "2026-10-08T12:34:56", "yesterday", "2026-02-30T12:34:56Z", "2026-10-08T12:34:56+25:00", "", 123):
            payload = result()
            payload["observations"][0]["acquired_at"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ContractError):
                validate_result(payload)

    def test_time_and_coordinate_frame_are_explicit_required_nullable_fields(self):
        payload = result()
        validate_result(payload)
        for field in ("acquired_at", "coordinate_frame"):
            missing = result()
            del missing["observations"][0][field]
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_result(missing)
        for frame in ("", " ", 123, {}):
            payload = result()
            payload["observations"][0]["coordinate_frame"] = frame
            with self.subTest(frame=frame), self.assertRaises(ContractError):
                validate_result(payload)

    def test_error_does_not_echo_supplied_values(self):
        payload = result()
        payload["module_id"] = "synthetic-private-value invalid"
        with self.assertRaises(ContractError) as error:
            validate_result(payload)
        self.assertNotIn("synthetic-private-value", str(error.exception))


class SchemaArtifactTests(unittest.TestCase):
    def test_draft_schema_documents_match_wire_envelopes(self):
        base = Path(__file__).resolve().parents[1] / "src" / "dorsopathy_platform" / "schemas" / "v1"
        for filename, fixture in (("module_request.schema.json", request()), ("module_result.schema.json", result())):
            schema = json.loads((base / filename).read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema["properties"]["schema_version"]["const"], SCHEMA_VERSION)
            self.assertEqual(set(schema["required"]), set(fixture))
            self.assertFalse(schema["additionalProperties"])


class StrictJSONTests(unittest.TestCase):
    def test_json_decoder_preserves_false_zero_null_and_supports_bytes(self):
        expected = {"nested": [False, 0, None, {"score": 6}]}
        encoded = json.dumps(expected)
        self.assertEqual(loads_json(encoded), expected)
        self.assertEqual(loads_json(encoded.encode("utf-8")), expected)

    def test_duplicate_keys_are_rejected_at_any_level_without_echoing_key(self):
        for encoded in (
            '{"synthetic-private-key": false, "synthetic-private-key": true}',
            '{"outer": {"synthetic-private-key": 1, "synthetic-private-key": 2}}',
        ):
            with self.subTest(encoded=encoded), self.assertRaises(ContractError) as error:
                loads_json(encoded)
            self.assertNotIn("synthetic-private-key", str(error.exception))

    def test_nonfinite_constants_and_exponent_overflow_rejected(self):
        for encoded in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e999}'):
            with self.subTest(encoded=encoded), self.assertRaises(ContractError):
                loads_json(encoded)

    def test_deep_json_is_contract_error_even_beyond_parser_recursion_limit(self):
        for levels in (65, 10000):
            encoded = "[" * levels + "null" + "]" * levels
            with self.subTest(levels=levels), self.assertRaises(ContractError):
                loads_json(encoded)
        encoded = "[" * 64 + "null" + "]" * 64
        self.assertEqual(loads_json(encoded), json.loads(encoded))

    def test_invalid_encoding_malformed_json_and_python_extensions_rejected(self):
        for encoded in (b"\xff", "{broken", 1, bytearray(b"{}")):
            with self.subTest(encoded=encoded), self.assertRaises(ContractError):
                loads_json(encoded)


if __name__ == "__main__":
    unittest.main()
