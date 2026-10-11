"""Software/import checks with synthetic rows; no clinical or vendor-byte validation."""
from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree
from unittest.mock import patch

from dorsopathy_modules.nociception_raw import RawNociceptionProvider
from dorsopathy_modules.nociception_raw.profile import HEADERS
from dorsopathy_platform.contracts import ContractError, loads_json, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "nociception_raw"


def csv_bytes(rows, headers=HEADERS):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(headers)
    writer.writerows(rows)
    return text.getvalue().encode()


class RawNociceptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = ArtifactStore(self.root)
        self.provider = RawNociceptionProvider()
        self.manifest = json.loads((FIXTURES / "synthetic-acquisition.json").read_text())
        self.rows = [[0, 20, 70, ""], [1, 30, 71, ""], [2, 40, 72, ""],
                     [3, 70, 90, "Synthetic event A"], [4, 60, 85, ""], [5, 50, 80, ""]]

    def prepare(self, rows=None, manifest=None, raw=None, headers=HEADERS, parameters=None):
        source = self.store.add_bytes(raw if raw is not None else csv_bytes(self.rows if rows is None else rows, headers), "text/csv")
        data = deepcopy(self.manifest if manifest is None else manifest)
        if data["csv_artifact_id"] == "bind-on-import":
            data["csv_artifact_id"] = source["artifact_id"]
        manifest_ref = self.store.add_bytes(json.dumps(data, allow_nan=False).encode(), "application/json")
        return {"schema_version": "1.0", "job_id": "job-noci-raw", "episode_id": "episode-synthetic",
                "module_id": "nociception", "module_version": "0.2.0", "input_artifacts": [source, manifest_ref],
                "parameters": parameters or {}, "protocol_id": data["protocol_id"]}

    def execute(self, **kwargs):
        request = self.prepare(**kwargs)
        result = self.provider.analyze(request, ProviderContext(self.store))
        return validate_result(result, request)

    def derived(self, result, kind):
        for ref in result["artifacts"]:
            if ref["media_type"] == "application/json":
                value = loads_json(self.store.read(ref))
                if value.get("source_kind") == kind:
                    return value
        self.fail("Missing expected derived evidence")

    def test_synthetic_documented_fields_are_imported_with_unknown_quality_not_proprietary_recalculation(self):
        result = self.execute()
        self.assertEqual(result["status"], "partial")
        self.assertEqual([obs["concept"] for obs in result["observations"]], ["nociception.device_index", "nociception.heart_rate", "nociception.event_response_delta"])
        self.assertEqual([obs["value"] for obs in result["observations"]], [45, 78, None])
        self.assertEqual([obs["unit"] for obs in result["observations"]], ["1", "/min", "1"])
        self.assertTrue(all(obs["quality"] == "unknown" and obs["origin"] == "synthetic_demo" and obs["model"] is None for obs in result["observations"]))
        self.assertTrue(all(obs["evidence_refs"] for obs in result["observations"]))
        self.assertTrue(all(obs["acquired_at"] is None for obs in result["observations"]))
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertEqual([row["time_s"] for row in series["rows"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual([row["nol_index"] for row in series["rows"]], [20, 30, 40, 70, 60, 50])
        self.assertTrue(all(row["raw_quality"] is None and row["quality"] == "unknown" for row in series["rows"]))
        self.assertEqual(series["qc"]["quality_usable_count"], 0)
        summary = self.derived(result, "nociception.raw-measurements.v2")
        self.assertEqual(summary["events"][0]["time_s"], 3)
        self.assertEqual(summary["event_responses"][0]["assessment_status"], "not_assessed")
        self.assertIsNone(summary["event_responses"][0]["delta"])
        self.assertFalse(summary["clinical_applicability"]["eligible_for_dorsopathy_inference"])

    def test_changed_recorded_indices_change_mean_and_explicit_zero_remains_an_assessed_recorded_score(self):
        rows = deepcopy(self.rows)
        rows[0][1] = 80
        self.assertEqual(self.execute(rows=rows)["observations"][0]["value"], 55)
        for row in rows:
            row[1] = 0
        result = self.execute(rows=rows)
        self.assertEqual(result["observations"][0]["value"], 0)
        self.assertEqual(result["observations"][0]["assessment_status"], "assessed")
        self.assertEqual(result["observations"][0]["quality"], "unknown")

    def test_missing_index_remains_null_blocks_index_summary_and_preserves_heart_rate(self):
        rows = deepcopy(self.rows)
        rows[1][1] = ""
        result = self.execute(rows=rows)
        self.assertIsNone(result["observations"][0]["value"])
        self.assertEqual(result["observations"][1]["value"], 78)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertIsNone(series["rows"][1]["nol_index"])
        self.assertEqual(series["rows"][1]["quality"], "unreliable")
        self.assertEqual(series["qc"]["missing_or_invalid_nol_count"], 1)

    def test_nan_infinity_out_of_range_and_bad_tokens_are_qc_invalid_and_never_numeric_or_zero(self):
        for value in ("NaN", "inf", "1e999", "-1", "101", "not-a-number"):
            rows = deepcopy(self.rows)
            rows[0][1] = value
            result = self.execute(rows=rows)
            self.assertIsNone(result["observations"][0]["value"])
            self.assertIsNone(self.derived(result, "nociception.raw-series.v2")["rows"][0]["nol_index"])
        rows = deepcopy(self.rows)
        rows[0][2] = -1
        self.assertIsNone(self.execute(rows=rows)["observations"][1]["value"])

    def test_duplicates_nonmonotonic_times_and_gaps_are_preserved_without_sorting_or_interpolation(self):
        for value, reason, count in ((1, "duplicate_timestamp", "duplicate_timestamp_count"),
                                     (.5, "nonmonotonic_timestamp", "nonmonotonic_timestamp_count"),
                                     (10, "timestamp_gap", "gap_count")):
            rows = deepcopy(self.rows)
            rows[2][0] = value
            result = self.execute(rows=rows)
            series = self.derived(result, "nociception.raw-series.v2")
            self.assertEqual(len(series["rows"]), 6)
            self.assertEqual(series["rows"][2]["time_s"], value)
            self.assertIn(reason, series["rows"][2]["qc_reasons"])
            self.assertGreater(series["qc"][count], 0)
            self.assertIsNone(result["observations"][0]["value"])
            self.assertIsNone(result["observations"][1]["value"])
        duplicate = deepcopy(self.rows)
        duplicate[2][0] = 1
        series = self.derived(self.execute(rows=duplicate), "nociception.raw-series.v2")
        self.assertIn("duplicate_timestamp", series["rows"][1]["qc_reasons"])

    def test_unknown_cadence_is_not_inferred_and_points_do_not_claim_continuity(self):
        data = deepcopy(self.manifest)
        data["sampling_period_s"] = None
        result = self.execute(manifest=data)
        self.assertEqual(result["observations"][0]["value"], 45)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertIsNone(series["qc"]["declared_sampling_period_s"])
        self.assertEqual(series["qc"]["sampling_period_source"], "unknown")
        svg = self.store.read(next(ref for ref in result["artifacts"] if ref["media_type"] == "image/svg+xml")).decode()
        self.assertEqual(svg.count('<circle '), 6)
        self.assertNotIn('stroke="#f5b53f" stroke-dasharray=', svg)

    def test_elapsed_time_dialect_has_no_automatic_midnight_rollover(self):
        data = deepcopy(self.manifest)
        data["time_encoding"] = "elapsed_hh_mm_ss"
        rows = deepcopy(self.rows)
        for index, row in enumerate(rows):
            row[0] = "00:00:" + str(index).zfill(2)
        result = self.execute(rows=rows, manifest=data)
        self.assertEqual(result["observations"][0]["value"], 45)
        rows[0][0], rows[1][0] = "23:59:59", "00:00:00"
        result = self.execute(rows=rows, manifest=data)
        self.assertIsNone(result["observations"][0]["value"])
        self.assertGreater(self.derived(result, "nociception.raw-series.v2")["qc"]["nonmonotonic_timestamp_count"], 0)

    def test_naive_iso_time_retains_unknown_timezone_and_offset_iso_preserves_source_offset(self):
        data = deepcopy(self.manifest)
        data["time_encoding"] = "iso8601"
        rows = deepcopy(self.rows)
        for index, row in enumerate(rows):
            row[0] = "2020-01-01T12:00:" + str(index).zfill(2)
        result = self.execute(rows=rows, manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertFalse(series["qc"]["global_timezone_known"])
        self.assertTrue(all(row["acquired_at"] is None for row in series["rows"]))
        self.assertEqual([row["time_s"] for row in series["rows"]], [0, 1, 2, 3, 4, 5])
        for row in rows:
            row[0] += "+03:00"
        result = self.execute(rows=rows, manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertTrue(series["qc"]["global_timezone_known"])
        self.assertEqual(series["rows"][0]["acquired_at"], "2020-01-01T12:00:00+03:00")
        self.assertEqual(result["observations"][0]["acquired_at"], "2020-01-01T12:00:00+03:00")
        self.assertEqual(result["observations"][0]["quality"], "unknown")

    def test_anchor_conflict_and_mixed_timezones_block_alignment(self):
        data = deepcopy(self.manifest)
        data.update(time_encoding="iso8601", acquired_at="2020-01-01T13:00:00+03:00")
        rows = deepcopy(self.rows)
        for index, row in enumerate(rows):
            row[0] = "2020-01-01T12:00:" + str(index).zfill(2) + "+03:00"
        result = self.execute(rows=rows, manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertTrue(series["qc"]["clock_conflict"])
        self.assertTrue(all(row["acquired_at"] is None for row in series["rows"]))
        self.assertIsNone(result["observations"][0]["value"])
        data["acquired_at"] = None
        rows[1][0] = rows[1][0][:-6]
        result = self.execute(rows=rows, manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertIn("mixed_timezone_encoding", series["rows"][1]["qc_reasons"])
        self.assertFalse(series["qc"]["global_timezone_known"])
        self.assertEqual(series["qc"]["absolute_clock_comparability"], "unknown")
        self.assertIsNone(self.derived(result, "nociception.raw-measurements.v2")["comparison_metadata"]["acquired_interval"]["start"])
        self.assertIsNone(result["observations"][0]["acquired_at"])
        self.assertIsNone(result["observations"][0]["value"])

    def test_absolute_overflow_and_disordered_known_clock_do_not_create_whole_file_acquisition_interval(self):
        data = deepcopy(self.manifest)
        data.update(acquired_at="9999-12-31T23:59:59+00:00", clock_domain="synthetic-clock")
        result = self.execute(manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertIn("absolute_time_overflow", series["rows"][1]["qc_reasons"])
        self.assertFalse(series["qc"]["global_timezone_known"])
        self.assertIsNone(result["observations"][0]["acquired_at"])
        self.assertIsNone(result["observations"][0]["value"])
        data["acquired_at"] = "2020-01-01T12:00:00+00:00"
        rows = deepcopy(self.rows)
        rows[0][0], rows[-1][0] = 5, 3
        result = self.execute(rows=rows, manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertIsNotNone(series["rows"][0]["acquired_at"])
        self.assertFalse(series["qc"]["temporal_integrity_complete"])
        self.assertEqual(series["qc"]["absolute_clock_comparability"], "unknown")
        self.assertIsNone(self.derived(result, "nociception.raw-measurements.v2")["comparison_metadata"]["acquired_interval"]["start"])

    def test_source_anchor_known_for_elapsed_time_is_preserved_and_never_makes_quality_usable(self):
        data = deepcopy(self.manifest)
        data.update(acquired_at="2020-01-01T12:00:00+03:00", clock_domain="synthetic-clock")
        result = self.execute(manifest=data)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertEqual(series["rows"][5]["acquired_at"], "2020-01-01T12:00:05+03:00")
        self.assertEqual(result["observations"][0]["quality"], "unknown")
        self.assertIsNone(result["observations"][2]["value"])

    def test_recorded_annotations_preserve_quoted_commas_and_unknown_event_times_and_escape_plot(self):
        rows = deepcopy(self.rows)
        rows[3][3] = '<script>alert("x,y")</script>'
        result = self.execute(rows=rows)
        series = self.derived(result, "nociception.raw-series.v2")
        self.assertEqual(series["events"][0]["label"], rows[3][3])
        self.assertFalse(series["events"][0]["physical_stimulus_onset_confirmed"])
        svg = self.store.read(next(ref for ref in result["artifacts"] if ref["media_type"] == "image/svg+xml")).decode()
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)
        rows[3][3] = "Synthetic event\x01A\ufffe"
        result = self.execute(rows=rows)
        self.assertEqual(self.derived(result, "nociception.raw-series.v2")["events"][0]["label"], rows[3][3])
        svg = self.store.read(next(ref for ref in result["artifacts"] if ref["media_type"] == "image/svg+xml")).decode()
        ElementTree.fromstring(svg)
        self.assertIn("\ufffd", svg)
        rows[3][0] = ""
        result = self.execute(rows=rows)
        summary = self.derived(result, "nociception.raw-measurements.v2")
        self.assertIsNone(summary["events"][0]["time_s"])
        self.assertEqual(summary["event_responses"][0]["reason"], "annotation_time_unknown")

    def test_no_annotations_never_becomes_a_negative_nociceptive_finding(self):
        rows = deepcopy(self.rows)
        rows[3][3] = ""
        result = self.execute(rows=rows)
        self.assertEqual(result["observations"][2]["assessment_status"], "not_assessed")
        self.assertIsNone(result["observations"][2]["value"])
        self.assertEqual(self.derived(result, "nociception.raw-measurements.v2")["event_response_reason"], "no_recorded_annotations")

    def test_invalid_calibration_and_missing_time_never_become_a_normal_index(self):
        data = deepcopy(self.manifest)
        data["calibration"]["status"] = "invalid"
        result = self.execute(manifest=data)
        self.assertTrue(all(obs["value"] is None and obs["quality"] == "unreliable" for obs in result["observations"]))
        rows = deepcopy(self.rows)
        rows[0][0] = ""
        result = self.execute(rows=rows)
        self.assertTrue(all(obs["value"] is None for obs in result["observations"]))

    def test_exact_vendor_header_rejects_ani_ppg_aliases_extra_quality_and_arbitrary_csv(self):
        for headers in (("time_s", "NOL", "HR", "Events"), ("Time", "ANI", "Heart rate", "Annotations"),
                        ("Time", "NOL index", "Heart rate", "Annotations", "Quality"), ("Time", "PPG", "Heart rate", "Annotations")):
            result = self.execute(headers=headers)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["observations"], [])
        self.assertEqual(self.execute(raw=b"Time,NOL index,Heart rate,Annotations\n")["status"], "failed")

    def test_origin_context_binding_manual_metadata_and_no_commands_are_strict(self):
        for field, value in (("csv_artifact_id", "other-artifact"), ("vendor_profile", "arbitrary-csv"),
                             ("clinical_context", "normal"), ("time_encoding", "guess"),
                             ("acquired_at", "2020-01-01T00:00:00"), ("raw_ppg", []), ("quality", "usable")):
            data = deepcopy(self.manifest)
            data[field] = value
            with self.assertRaises(ContractError):
                self.execute(manifest=data)
        data = deepcopy(self.manifest)
        data["origin"] = "automatically_measured"
        with self.assertRaises(ContractError):
            self.execute(manifest=data)
        for parameters in ({"command": ["untrusted"]}, {"path": "/host/patient.csv"}):
            with self.assertRaises(ContractError):
                self.execute(parameters=parameters)
        data = deepcopy(self.manifest)
        data["missing_tokens"] = ["0"]
        with self.assertRaises(ContractError):
            self.execute(manifest=data)

    def test_malformed_non_string_profile_enums_are_contract_errors(self):
        for key in ("acquisition_mode", "origin", "clinical_context", "time_encoding"):
            data = deepcopy(self.manifest)
            data[key] = []
            with self.assertRaises(ContractError):
                self.execute(manifest=data)
        for parent, key in (("calibration", "status"), ("source_provenance", "license_status")):
            data = deepcopy(self.manifest)
            data[parent][key] = {}
            with self.assertRaises(ContractError):
                self.execute(manifest=data)

    def test_context_is_preserved_and_recorded_mode_has_no_pain_or_dorsopathy_inference(self):
        for clinical_context in ("awake", "general_anaesthesia", "unknown"):
            data = deepcopy(self.manifest)
            data.update(acquisition_mode="recorded", origin="automatically_measured", clinical_context=clinical_context)
            data["source_provenance"].update(license_status="unknown")
            result = self.execute(manifest=data)
            self.assertTrue(all(obs["origin"] == "automatically_measured" for obs in result["observations"]))
            summary = self.derived(result, "nociception.raw-measurements.v2")
            self.assertEqual(summary["clinical_context"], clinical_context)
            self.assertFalse(summary["clinical_applicability"]["eligible_for_dorsopathy_inference"])
            self.assertTrue(all(obs["quality"] == "unknown" for obs in result["observations"]))

    def test_actual_subprocess_imports_derived_evidence_using_verified_exchange(self):
        request = self.prepare()
        code = "import sys; sys.path.insert(0, " + repr(str(ROOT / "src")) + "); from dorsopathy_workers.nociception.worker import main; raise SystemExit(main())"
        provider = ProcessProvider(RawNociceptionProvider.spec, (sys.executable, "-c", code), self.root / "workers", timeout_seconds=30)
        self.addCleanup(provider.close)
        result = provider.analyze(request, ProviderContext(self.store))
        validate_result(result, request)
        self.assertEqual(result["observations"][0]["value"], 45)
        self.assertEqual(result["observations"][0]["origin"], "synthetic_demo")
        self.assertEqual(self.derived(result, "nociception.raw-series.v2")["qc"]["quality_usable_count"], 0)
        self.assertTrue(any(ref["media_type"] == "image/png" for ref in result["artifacts"]))

    def test_provider_and_worker_import_have_no_heavy_dependencies(self):
        code = "import json,sys; from dorsopathy_modules.nociception_raw import RawNociceptionProvider; import dorsopathy_workers.nociception.worker; print(json.dumps({name:name in sys.modules for name in ['numpy','pandas','scipy','torch']}))"
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, env=environment)
        self.assertEqual(json.loads(result.stdout), {"numpy": False, "pandas": False, "scipy": False, "torch": False})


if __name__ == "__main__":
    unittest.main()
