from copy import deepcopy
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

from dorsopathy_modules.lidar_posture import LidarPostureProvider
from dorsopathy_modules.lidar_raw import RawLidarProvider
from dorsopathy_modules.lidar_raw.analysis import validate_manifest
from dorsopathy_modules.lidar_raw.depth_png import decode_depth_png
from dorsopathy_modules.lidar_raw.geometry import camera_from_bytes, inverse_brown, project_pixel
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def depth_png(matrix):
    height, width = len(matrix), len(matrix[0])
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    raw = b"".join(b"\0" + struct.pack(">" + str(width) + "H", *row) for row in matrix)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def plane():
    rows = [[0] * 64 for _ in range(64)]
    for v in range(10, 56):
        for u in range(20, 44):
            rows[v][u] = 1000
    for v in (1, 2):
        for u in (1, 2):
            rows[v][u] = 1000
    return rows


class RawDepthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ArtifactStore(Path(self.directory.name) / "artifacts")
        self.data = json.loads((ROOT / "examples/lidar_raw/synthetic-acquisition.json").read_text())
        self.camera = json.loads((ROOT / "examples/lidar_raw/synthetic-camera.json").read_text())

    def execute(self, data=None, matrix=None, camera=None, process=False):
        data = deepcopy(data or self.data)
        camera = camera if camera is not None else self.camera
        depth_ref = self.store.add_bytes(depth_png(matrix or plane()), "image/png")
        camera_ref = self.store.add_bytes(json.dumps(camera, allow_nan=False).encode() if type(camera) is dict else camera, "application/json" if data["camera_format"] == "depth-camera-calibration.v1" else "application/xml")
        data["depth_artifact_id"], data["camera_artifact_id"] = depth_ref["artifact_id"], camera_ref["artifact_id"]
        source = self.store.add_bytes(json.dumps(data, allow_nan=False).encode(), "application/json")
        request = {
            "schema_version": "1.0", "job_id": "raw-depth-test", "episode_id": "synthetic-depth-test",
            "module_id": "lidar.posture", "module_version": "0.2.0", "protocol_id": data["protocol_id"],
            "input_artifacts": [source, depth_ref, camera_ref], "parameters": {},
        }
        provider = RawLidarProvider(python_executable=sys.executable if process else None, workspace_root=Path(self.directory.name) / "workers")
        self.addCleanup(provider.close)
        result = provider.analyze(request, ProviderContext(self.store))
        summaries = [json.loads(self.store.read(reference)) for reference in result["artifacts"] if reference["role"] == "derived" and reference["media_type"] == "application/json"]
        summary = next(value for value in summaries if value.get("source_kind") == "lidar.depth.measurements.v2")
        return result, summary

    def test_versioned_subprocess_and_known_plane_geometry(self):
        self.assertEqual(LidarPostureProvider.spec.module_version, "0.1.0")
        result, summary = self.execute(process=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(summary["qc"]["largest_component_pixels"], 1104)
        self.assertEqual(summary["comparison_metadata"]["calibration_status"], "verified")
        self.assertEqual(summary["qc"]["candidate_pixels"], 1108)
        self.assertEqual([obs["value"] for obs in result["observations"][:3]], [230, 450, 0])
        self.assertEqual([obs["value"] for obs in result["observations"][3:6]], [450, 230, 0])
        self.assertEqual(result["observations"][6]["value"], 0)
        self.assertTrue(all(obs["origin"] == "synthetic_demo" and obs["evidence_refs"] for obs in result["observations"]))

    def test_unknown_metric_calibration_does_not_guess_body_height_or_mm(self):
        data = deepcopy(self.data)
        data["calibration"] = {"status": "unknown", "source": None, "author_id": None, "mm_per_unit": None}
        result, summary = self.execute(data)
        self.assertTrue(all(obs["value"] is None for obs in result["observations"][:6]))
        self.assertEqual(summary["geometry_unit"], "source_depth_units")
        self.assertEqual(result["observations"][6]["value"], 0)
        self.assertEqual(result["status"], "partial")

    def test_unknown_orientation_blocks_anatomical_tilt_but_retains_sensor_extents(self):
        data = deepcopy(self.data)
        data["axes"], data["coordinate_frame"] = None, None
        data["anatomical_frame"] = {"status": "unknown", "up_vector_camera": None, "source": None, "author_id": None}
        result, summary = self.execute(data)
        self.assertEqual(result["observations"][0]["value"], 230)
        self.assertIsNone(result["observations"][-1]["value"])
        self.assertIsNone(summary["anatomical_asymmetry"])
        self.assertIsNone(summary["automatic_anatomical_landmarks"])

    def test_invalid_anatomical_axes_and_manual_landmarks_are_rejected(self):
        data = deepcopy(self.data)
        data["axes"] = ["left", "up", "forward"]
        with self.assertRaises(ContractError):
            validate_manifest(data)
        data = deepcopy(self.data)
        data["annotations"] = {"landmark_vertex": 3}
        with self.assertRaises(ContractError):
            validate_manifest(data)
        data = deepcopy(self.data)
        data["anatomical_frame"]["up_vector_camera"] = [0, -2, 0]
        with self.assertRaises(ContractError):
            validate_manifest(data)

    def test_malformed_identifiers_and_metadata_types_fail_contract(self):
        for field in ("depth_artifact_id", "camera_artifact_id", "camera_format", "origin"):
            data = deepcopy(self.data)
            data[field] = ["invalid"]
            with self.assertRaises(ContractError):
                validate_manifest(data)
        camera = deepcopy(self.camera)
        camera["distortion"]["model"] = ["unknown"]
        with self.assertRaises(ContractError):
            camera_from_bytes(json.dumps(camera).encode(), "depth-camera-calibration.v1")

    def test_unknown_distortion_retains_nominal_geometry_as_unreliable(self):
        camera = deepcopy(self.camera)
        camera["distortion"] = {"model": "unknown", "coefficients": None, "source": None}
        result, summary = self.execute(camera=camera)
        self.assertEqual(result["observations"][0]["value"], 230)
        self.assertTrue(all(obs["quality"] == "unreliable" for obs in result["observations"]))
        self.assertFalse(summary["qc"]["geometry_distortion_verified"])
        self.assertEqual(summary["calibration"]["status"], "verified")
        self.assertEqual(summary["comparison_metadata"]["calibration_status"], "unknown")
        self.assertEqual(summary["comparison_metadata"]["calibration_evidence_refs"][0]["selector"]["scope"], "depth_unit_scale_only")

    def test_brown_conrady_inverse_matches_independent_forward_model(self):
        coefficients = [0.12, -0.025, 0.002, -0.003, 0.005]
        x, y = 0.35, -0.22
        k1, k2, p1, p2, k3 = coefficients
        radius = x * x + y * y
        radial = 1 + k1 * radius + k2 * radius ** 2 + k3 * radius ** 3
        xd = x * radial + 2 * p1 * x * y + p2 * (radius + 2 * x * x)
        yd = y * radial + p1 * (radius + 2 * y * y) + 2 * p2 * x * y
        recovered = inverse_brown(xd, yd, coefficients)
        self.assertAlmostEqual(recovered[0], x, places=10)
        self.assertAlmostEqual(recovered[1], y, places=10)
        camera = deepcopy(self.camera)
        camera["distortion"] = {"model": "brown_conrady", "coefficients": coefficients, "source": "synthetic lens coefficients"}
        intr = camera["intrinsics"]
        point = project_pixel(intr["cx"] + xd * intr["fx"], intr["cy"] + yd * intr["fy"], 1000, camera, 1)
        for actual, expected in zip(point, [350, -220, 1000]):
            self.assertAlmostEqual(actual, expected, places=7)

    def test_missing_zero_depth_empty_component_and_size_mismatch_are_explicit(self):
        result, summary = self.execute(matrix=[[0] * 64 for _ in range(64)])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(all(obs["value"] is None for obs in result["observations"]))
        self.assertEqual(summary["qc"]["point_count"], 0)
        self.assertEqual(summary["comparison_metadata"]["calibration_status"], "invalid")
        camera = deepcopy(self.camera)
        camera["width"] = 63
        with self.assertRaises(ContractError):
            self.execute(camera=camera)

    def test_corrupt_crc_non_depth_png_trailing_data_and_bomb_dimensions_rejected(self):
        data = bytearray(depth_png(plane()))
        data[-1] ^= 1
        invalids = [bytes(data), depth_png(plane()) + b"extra", b"not-png"]
        for width, height, bits, color in ((4096, 4096, 16, 0), (64, 64, 8, 0), (64, 64, 16, 2)):
            bad_header = bytearray(depth_png(plane()))
            payload = struct.pack(">IIBBBBB", width, height, bits, color, 0, 0, 0)
            bad_header[16:29] = payload
            bad_header[29:33] = struct.pack(">I", zlib.crc32(b"IHDR" + payload) & 0xffffffff)
            invalids.append(bytes(bad_header))
        for invalid in invalids:
            with self.assertRaises(ContractError):
                decode_depth_png(io.BytesIO(invalid))

    def test_texel_xml_uses_original_intrinsics_and_never_assumes_rectification(self):
        raw = b'<?xml version="1.0" encoding="utf-16"?><texel><scan><stage><stream><depth width="64" height="64"><intrinsics fx="100" fy="100" cx="31.5" cy="31.5"/></depth></stream></stage></scan></texel>'
        camera = camera_from_bytes(raw, "texel.scan.xml.v1")
        self.assertEqual(camera["intrinsics"]["fx"], 100)
        self.assertEqual(camera["distortion"]["model"], "unknown")
        data = deepcopy(self.data)
        data["camera_format"] = "texel.scan.xml.v1"
        result, summary = self.execute(data, camera=raw)
        self.assertEqual(summary["qc"]["point_count"], 1104)
        self.assertEqual(result["observations"][0]["quality"], "unreliable")
        with self.assertRaises(ContractError):
            camera_from_bytes(b'<!DOCTYPE texel [<!ENTITY x "abc">]><texel/>', "texel.scan.xml.v1")

    def test_raw_depth_change_updates_measured_geometry(self):
        result, before = self.execute()
        matrix = plane()
        for v in range(10, 56):
            for u in range(20, 44):
                matrix[v][u] = 1200
        result, after = self.execute(matrix=matrix)
        self.assertAlmostEqual(after["measurements"][0]["value"], before["measurements"][0]["value"] * 1.2)
        self.assertAlmostEqual(after["measurements"][1]["value"], before["measurements"][1]["value"] * 1.2)


if __name__ == "__main__":
    unittest.main()
