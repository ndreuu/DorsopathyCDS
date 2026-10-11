"""Software acceptance; synthetic arithmetic is not clinical validation."""
from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from dorsopathy_modules.thermography_raw import RawThermographyProvider
from dorsopathy_modules.thermography_raw.decoder import decode_sensor_png
from dorsopathy_modules.thermography_raw.radiometry import convert_counts
from dorsopathy_modules.thermography_raw.regions import automatic_foreground
from dorsopathy_platform.contracts import ContractError, loads_json, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)


def sensor_png(counts, byte_order="little", filters=None):
    width, height = len(counts[0]), len(counts)
    raw, previous = bytearray(), bytearray(width * 2)
    for y, values in enumerate(counts):
        row = bytearray(struct.pack(("<" if byte_order == "little" else ">") + str(width) + "H", *values))
        filtered = bytearray()
        mode = filters[y] if filters else 0
        for index, value in enumerate(row):
            left = row[index - 2] if index >= 2 else 0
            up, corner = previous[index], previous[index - 2] if index >= 2 else 0
            if mode == 0: predictor = 0
            elif mode == 1: predictor = left
            elif mode == 2: predictor = up
            elif mode == 3: predictor = (left + up) // 2
            else:
                estimate = left + up - corner
                candidates = (left, up, corner)
                predictor = candidates[min(range(3), key=lambda k: abs(estimate - candidates[k]))]
            filtered.append((value - predictor) & 255)
        raw.extend(bytes([mode]) + filtered)
        previous = row
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def metadata():
    return {"FLIR:" + name: value for name, value in {
        "PlanckR1": 15310.16015625, "PlanckR2": 0.0276533886790276,
        "PlanckB": 1408.90002441406, "PlanckF": 2.5, "PlanckO": -7586,
        "AtmosphericTransAlpha1": .006569, "AtmosphericTransAlpha2": .01262,
        "AtmosphericTransBeta1": -.002276, "AtmosphericTransBeta2": -.00667, "AtmosphericTransX": 1.9,
        "Emissivity": 1, "ObjectDistance": 0, "ReflectedApparentTemperature": 20,
        "AtmosphericTemperature": 20, "IRWindowTemperature": 20, "IRWindowTransmission": 1,
        "RelativeHumidity": .5, "RawThermalImageWidth": 8, "RawThermalImageHeight": 8,
        "RawThermalImageType": "PNG", "CameraModel": "synthetic-FLIR-calibration",
    }.items()}


def counts_fixture():
    return [[13200 + (x + y) if 2 <= x < 6 and 2 <= y < 6 else 12500 for x in range(8)] for y in range(8)]


def manifest_fixture(jpeg_id):
    return {"schema_version": "1.0", "source_kind": "thermography-raw-flir.v1", "jpeg_artifact_id": jpeg_id,
            "acquisition_mode": "synthetic", "origin": "synthetic_demo", "subject_ref": "subject-synthetic",
            "session_ref": "session-synthetic", "protocol_id": "synthetic-static-v1", "acquired_at": None,
            "clock_domain": None, "pose": None, "coordinate_frame": "synthetic-native-thermal-pixels",
            "count_byte_order": "little", "calibration": {"status": "unknown", "source": None, "author_id": None},
            "conditions": {"status": "file_settings_only", "source": None, "emissivity": None, "distance_m": None,
                           "reflected_Cel": None, "ambient_Cel": None, "window_Cel": None,
                           "window_transmission": None, "relative_humidity_fraction": None},
            "source_provenance": {"source_url": None, "source_revision": None, "license_status": "synthetic", "license_identifier": None}}


class RawThermographyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ArtifactStore(Path(self.directory.name))
        self.jpeg = self.store.add_bytes(b"\xff\xd8synthetic-decoder-test-only\xff\xd9", "image/jpeg")
        self.manifest = manifest_fixture(self.jpeg["artifact_id"])
        self.counts = counts_fixture()
        self.tags = metadata()
        self.provider = RawThermographyProvider(("trusted-exiftool",))

    def execute(self, manifest=None, counts=None, tags=None, parameters=None, decoder_error=False):
        data = deepcopy(manifest or self.manifest)
        manifest_ref = self.store.add_bytes(json.dumps(data, allow_nan=False).encode(), "application/json")
        request = {"schema_version": "1.0", "job_id": "job-thermal-raw", "episode_id": "episode-synthetic",
                   "module_id": "thermography", "module_version": "0.2.0", "input_artifacts": [self.jpeg, manifest_ref],
                   "parameters": parameters or {}, "protocol_id": data["protocol_id"]}
        count_data = counts if counts is not None else self.counts
        extraction = {"counts": count_data, "metadata": tags or self.tags, "sensor_png": sensor_png(count_data),
                      "decoder": {"id": "synthetic-test-double", "version": "1.0", "source": None, "license": None}}
        with patch("dorsopathy_modules.thermography_raw.provider.extract_flir", side_effect=ContractError("unsupported") if decoder_error else None, return_value=extraction):
            result = self.provider.analyze(request, ProviderContext(self.store))
        validate_result(result, request)
        return result

    def summary(self, result, kind="thermography.raw-measurements.v1"):
        for ref in result["artifacts"]:
            if ref["media_type"] == "application/json":
                value = loads_json(self.store.read(ref))
                if value.get("source_kind") == kind:
                    return value
        self.fail("Missing derived JSON evidence")

    def test_raw_counts_generate_automatic_roi_celsius_and_unknown_anatomical_pair(self):
        result = self.execute()
        self.assertEqual(result["status"], "partial")
        self.assertEqual([obs["concept"] for obs in result["observations"]], ["thermal.roi_mean_temperature", "thermal.roi_max_temperature", "thermal.left_right_delta"])
        self.assertEqual([obs["unit"] for obs in result["observations"]], ["Cel", "Cel", "K"])
        summary = self.summary(result)
        self.assertEqual(summary["region"]["pixel_count"], 16)
        self.assertEqual(summary["region"]["bounding_box_xyxy"], [2, 2, 6, 6])
        temperatures = [self.tags["FLIR:PlanckB"] / math.log(self.tags["FLIR:PlanckR1"] / (self.tags["FLIR:PlanckR2"] * (self.counts[y][x] + self.tags["FLIR:PlanckO"])) + self.tags["FLIR:PlanckF"]) - 273.15 for y in range(2, 6) for x in range(2, 6)]
        self.assertAlmostEqual(result["observations"][0]["value"], sum(temperatures) / 16, places=10)
        self.assertAlmostEqual(result["observations"][1]["value"], max(temperatures), places=10)
        self.assertIsNone(result["observations"][2]["value"])
        self.assertEqual(result["observations"][2]["assessment_status"], "not_assessed")
        for obs in result["observations"]:
            self.assertEqual(obs["anatomy"], {"region": "gross_thermal_foreground", "side": "unspecified", "level": None, "numbering_status": "not_applicable"})
            self.assertEqual(obs["quality"], "unknown")
            self.assertEqual(obs["origin"], "synthetic_demo")
            self.assertIsNone(obs["model"])
            self.assertTrue(obs["evidence_refs"])
        self.assertFalse(summary["region"]["validated_for_skin_or_body"])
        self.assertIn("Cel", self.store.read(next(ref for ref in result["artifacts"] if ref["media_type"] == "image/svg+xml")).decode())
        self.assertTrue(any(ref["artifact_id"] == summary["mask_artifact_id"] for ref in result["artifacts"]))
        self.assertEqual(summary["matrix_axes"], "row_y,column_x")

    def test_changing_sensor_counts_changes_the_measured_temperature(self):
        first = self.execute()
        changed = deepcopy(self.counts)
        changed[3][3] += 100
        second = self.execute(counts=changed)
        self.assertNotEqual(first["observations"][0]["value"], second["observations"][0]["value"])
        self.assertGreater(second["observations"][1]["value"], first["observations"][1]["value"])

    def test_missing_radiometry_and_false_color_decoder_failure_never_become_zero(self):
        for missing in ("FLIR:PlanckR1", "FLIR:Emissivity"):
            tags = deepcopy(self.tags)
            del tags[missing]
            result = self.execute(tags=tags)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["observations"], [])
        result = self.execute(decoder_error=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], [])

    def test_invalid_calibration_and_constant_roi_remain_unassessed(self):
        manifest = deepcopy(self.manifest)
        manifest["calibration"]["status"] = "invalid"
        result = self.execute(manifest=manifest)
        self.assertTrue(all(obs["value"] is None and obs["quality"] == "unreliable" for obs in result["observations"]))
        result = self.execute(counts=[[13000] * 8 for _ in range(8)])
        self.assertTrue(all(obs["value"] is None for obs in result["observations"]))
        self.assertEqual(self.summary(result)["region"]["reason"], "constant_temperature_frame")

    def test_saturated_pixels_are_null_and_qc_unreliable(self):
        counts = deepcopy(self.counts)
        counts[0][0] = 0
        result = self.execute(counts=counts)
        matrix = self.summary(result, "thermography.temperature-matrix.v1")
        self.assertIsNone(matrix["temperature_matrix"][0][0])
        self.assertEqual(self.summary(result)["radiometry"]["invalid_pixel_count"], 1)
        self.assertEqual(result["observations"][0]["quality"], "unreliable")

    def test_all_invalid_sensor_counts_produce_failed_without_observations(self):
        result = self.execute(counts=[[0] * 8 for _ in range(8)])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], [])

    def test_no_uploaded_roi_or_command_parameters_and_strict_source_binding(self):
        with self.assertRaises(ContractError):
            self.execute(parameters={"command": ["untrusted"]})
        for field, value in (("jpeg_artifact_id", "other-artifact"), ("rois", []), ("acquired_at", "2026-10-10T10:00:00")):
            manifest = deepcopy(self.manifest)
            manifest[field] = value
            with self.assertRaises(ContractError):
                self.execute(manifest=manifest)

    def test_verified_conditions_have_explicit_values_and_unknown_does_not_fabricate_accuracy(self):
        manifest = deepcopy(self.manifest)
        manifest["conditions"]["status"] = "independently_verified"
        with self.assertRaises(ContractError):
            self.execute(manifest=manifest)
        manifest["conditions"].update(status="unknown", emissivity=.95, source="synthetic-physical-settings")
        result = self.execute(manifest=manifest)
        summary = self.summary(result)
        self.assertEqual(summary["radiometry"]["condition_sources"]["emissivity"]["kind"], "acquisition_manifest")
        self.assertEqual(result["observations"][0]["quality"], "unknown")

    def test_invalid_physical_domains_fail_explicitly(self):
        for name, value in (("PlanckR1", -1), ("PlanckR2", 0), ("Emissivity", 0), ("IRWindowTransmission", 1.1), ("RelativeHumidity", 50), ("AtmosphericTemperature", -273.15)):
            tags = deepcopy(self.tags)
            tags["FLIR:" + name] = value
            self.assertEqual(self.execute(tags=tags)["status"], "failed")
        tags = deepcopy(self.tags)
        tags["FLIR:PlanckR1"] = float("nan")
        with self.assertRaises(ContractError):
            convert_counts(self.counts, tags, self.manifest["conditions"])


class SensorAndForegroundTests(unittest.TestCase):
    def test_crc_checked_png_unfilter_all_modes_and_both_declared_byte_orders(self):
        counts = [[10000 + x * 97 + y * 31 for x in range(7)] for y in range(5)]
        for order in ("little", "big"):
            self.assertEqual(decode_sensor_png(sensor_png(counts, order, [0, 1, 2, 3, 4]), order), counts)

    def test_png_checksum_truncation_wrong_depth_and_bomb_dimensions_rejected(self):
        valid = sensor_png([[12300] * 8 for _ in range(8)])
        bad_crc = bytearray(valid)
        bad_crc[-1] ^= 1
        unsupported = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"x")) + chunk(b"IEND", b"")
        oversized = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 16, 0, 0, 0, 0)) + chunk(b"IEND", b"")
        for payload in (bytes(bad_crc), valid[:-2], valid + b"trailing", unsupported, oversized):
            with self.assertRaises(ContractError):
                decode_sensor_png(payload, "little")

    def test_foreground_is_largest_component_with_reproducible_tie_and_no_anatomy(self):
        matrix = [[20.] * 16 for _ in range(8)]
        for x1 in (1, 10):
            for y in range(2, 6):
                for x in range(x1, x1 + 4):
                    matrix[y][x] = 34.
        first = automatic_foreground(matrix)
        self.assertEqual(first, automatic_foreground(matrix))
        self.assertEqual(first[1]["bounding_box_xyxy"], [1, 2, 5, 6])
        self.assertEqual(first[1]["side"], "unspecified")
        self.assertIsNone(first[1]["level"])
        self.assertFalse(first[1]["orientation_confirmed"])


if __name__ == "__main__":
    unittest.main()
