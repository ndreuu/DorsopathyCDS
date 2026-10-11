from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dorsopathy_modules.ultrasound._support import png
from dorsopathy_modules.ultrasound_raw import RawUltrasoundProvider
from dorsopathy_modules.ultrasound_raw.provider import mask_metrics
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


class RawUltrasoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name) / "artifacts")
        self.data = json.loads((ROOT / "examples/ultrasound_raw/source.json").read_text())
        self.data.update(acquisition_mode="synthetic", origin="synthetic_demo", plane="transverse")
        self.data["calibration"] = {"status": "verified", "source": "synthetic-anisotropic-reference", "author_id": "test-author",
            "mm_per_pixel_x": 2, "mm_per_pixel_y": 3, "region": [0, 0, 9, 9]}
        self.mask = [[int(2 <= x <= 5 and 3 <= y <= 6) for x in range(10)] for y in range(10)]
        self.pixels = png(10, 10, [bytearray([(x + y) * 8 for x in range(10) for _ in range(3)]) for y in range(10)])

    def request(self, data=None, pixels=None):
        data = deepcopy(self.data if data is None else data)
        frame = self.store.add_bytes(self.pixels if pixels is None else pixels, "image/png")
        data["frame_artifact_id"] = frame["artifact_id"]
        source = self.store.add_bytes(json.dumps(data).encode(), "application/json")
        return {"schema_version": "1.0", "job_id": "raw-us-test", "episode_id": "raw-us-independent", "module_id": "ultrasound",
            "module_version": "0.2.0", "input_artifacts": [source, frame], "parameters": {}, "protocol_id": data["protocol_id"]}

    def predicted(self, decoded, prompt):
        self.assertEqual(prompt, "multifidus_muscle")
        # Synthetic geometry reference, explicitly not a real model accuracy test.
        return {"mask": self.mask, "score": .9, "model": {"id": "synthetic-geometry-test", "version": "1", "weights_sha256": "0" * 64}, "status": "synthetic_test_only"}

    def analyze(self, data=None, segmenter=True, pixels=None):
        return RawUltrasoundProvider(self.predicted if segmenter else None).analyze(self.request(data, pixels), ProviderContext(self.store))

    def summary(self, result):
        for ref in result["artifacts"]:
            if ref["role"] == "derived" and ref["media_type"] == "application/json":
                return json.loads(self.store.read(ref))

    def test_raw_anisotropic_mask_area_and_pixel_cell_thickness(self):
        result = self.analyze()
        self.assertEqual([o["value"] for o in result["observations"]], [96, 12])
        self.assertTrue(all(o["origin"] == "synthetic_demo" for o in result["observations"]))
        self.assertTrue(all(o["quality"] == "unknown" for o in result["observations"]))
        summary = self.summary(result)
        self.assertEqual(summary["measurements"]["area_pixels"], 16)
        self.assertEqual(summary["diagnostic_interpretation"], "none")
        self.assertIn("mask_artifact_id", summary["segmentation"])

    def test_without_model_features_are_unassessed_and_source_qc_retained(self):
        result = self.analyze(segmenter=False)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(all(o["value"] is None and o["assessment_status"] == "not_assessed" for o in result["observations"]))
        self.assertEqual(self.summary(result)["segmentation"]["reason"], "model_not_configured")

    def test_unknown_scale_and_plane_are_distinct_no_zero(self):
        data = deepcopy(self.data); data["calibration"].update(status="unknown", mm_per_pixel_x=None, mm_per_pixel_y=None, region=None)
        self.assertTrue(all(o["value"] is None for o in self.analyze(data)["observations"]))
        data = deepcopy(self.data); data["plane"] = "unknown"
        result = self.analyze(data)
        self.assertTrue(all(o["value"] is None for o in result["observations"]))
        self.assertEqual(self.summary(result)["measurements"]["area_reason"], "transverse_acquisition_plane_required")

    def test_clipped_empty_and_disconnected_masks_do_not_become_zero_features(self):
        for mode in ("empty", "border", "disconnected"):
            with self.subTest(mode=mode):
                self.mask = [[0] * 10 for _ in range(10)]
                if mode == "border": self.mask[0][3] = 1
                if mode == "disconnected": self.mask[3][3] = 1; self.mask[7][7] = 1
                self.assertTrue(all(o["value"] is None for o in self.analyze()["observations"]))

    def test_manual_masks_rois_points_model_paths_and_parameters_rejected(self):
        for key in ("mask", "annotations", "polygon", "roi", "checkpoint", "model_path"):
            data = deepcopy(self.data); data[key] = []
            with self.subTest(key=key), self.assertRaises(ContractError): self.analyze(data)
        request = self.request(); request["parameters"] = {"checkpoint": "/tmp/model"}
        with self.assertRaises(ContractError): RawUltrasoundProvider().analyze(request, ProviderContext(self.store))

    def test_constant_frame_never_calls_detector(self):
        def forbidden(*args): self.fail("Constant source must not invoke detector")
        request = self.request(pixels=png(10, 10, [bytearray(30) for _ in range(10)]))
        result = RawUltrasoundProvider(forbidden).analyze(request, ProviderContext(self.store))
        self.assertEqual(self.summary(result)["measurements"]["area_reason"], "constant_source_frame")

    def test_raw_worker_protocol_without_optional_dependencies(self):
        bootstrap = "import sys; sys.path.insert(0, " + repr(str(ROOT / "src")) + "); from dorsopathy_workers.ultrasound.worker import main; raise SystemExit(main())"
        provider = ProcessProvider(RawUltrasoundProvider.spec, (sys.executable, "-c", bootstrap), Path(self.temp.name) / "worker", 30)
        try: result = provider.analyze(self.request(), ProviderContext(self.store))
        finally: provider.close()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 2)
        for ref in result["artifacts"]: self.store.read(ref)

    def test_runner_reuse_removes_stale_previews_and_failed_analysis(self):
        folder = Path(self.temp.name)
        frame = folder / "raw.png"; frame.write_bytes(self.pixels)
        manifest = folder / "acquisition.json"; manifest.write_text(json.dumps(self.data))
        output = folder / "result"; output.mkdir()
        for name in ("mask.png", "overlay.png", "analysis.json"):
            (output / name).write_bytes(b"stale prior result")
        command = [sys.executable, str(ROOT / "scripts/run_ultrasound_raw.py"),
            "--frame", str(frame), "--manifest", str(manifest), "--output-dir", str(output)]
        first = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertFalse((output / "mask.png").exists())
        self.assertFalse((output / "overlay.png").exists())
        self.assertEqual(json.loads((output / "analysis.json").read_text())["segmentation"]["reason"], "model_not_configured")
        frame.write_bytes(b"not a PNG")
        failed = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads((output / "result.json").read_text())["status"], "failed")
        self.assertFalse((output / "analysis.json").exists())
        self.assertFalse((output / "source.png").exists())


if __name__ == "__main__": unittest.main()
