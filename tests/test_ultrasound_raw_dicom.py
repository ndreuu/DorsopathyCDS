"""Optional-dependency raw DICOM tests; all studies are generated synthetic."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from dorsopathy_modules.ultrasound_raw import RawUltrasoundProvider
from dorsopathy_workers.ultrasound.imaging import load_frame
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

HAVE_DICOM = all(importlib.util.find_spec(x) for x in ("pydicom", "numpy"))
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAVE_DICOM, "optional pydicom/NumPy runtime unavailable")
class RawUltrasoundDicomTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, UltrasoundMultiFrameImageStorage, generate_uid
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))
        self.data = json.loads((ROOT / "examples/ultrasound_raw/source.json").read_text())
        self.data.update(acquisition_mode="synthetic", origin="synthetic_demo", plane="transverse")
        meta = FileMetaDataset(); meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = UltrasoundMultiFrameImageStorage; meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = meta.MediaStorageSOPClassUID; ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.Modality = "US"; ds.Rows = 10; ds.Columns = 10; ds.NumberOfFrames = 2
        ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7; ds.PixelRepresentation = 0
        ds.PixelData = np.stack([np.zeros((10, 10), np.uint8), np.arange(100, dtype=np.uint8).reshape(10, 10)]).tobytes()
        region = Dataset(); region.RegionSpatialFormat = 1; region.RegionDataType = 1
        region.PhysicalUnitsXDirection = 3; region.PhysicalUnitsYDirection = 3
        region.PhysicalDeltaX = -.02; region.PhysicalDeltaY = .03
        region.RegionLocationMinX0 = 0; region.RegionLocationMinY0 = 0
        region.RegionLocationMaxX1 = 9; region.RegionLocationMaxY1 = 9
        ds.SequenceOfUltrasoundRegions = [region]
        ds.PixelSpacing = [99, 99]
        self.ds = ds

    def analyze(self, data=None):
        import pydicom
        stream = io.BytesIO(); pydicom.dcmwrite(stream, self.ds, enforce_file_format=True)
        frame = self.store.add_bytes(stream.getvalue(), "application/dicom")
        data = deepcopy(self.data if data is None else data); data["frame_artifact_id"] = frame["artifact_id"]
        source = self.store.add_bytes(json.dumps(data).encode(), "application/json")
        request = {"schema_version": "1.0", "job_id": "raw-us-dicom", "episode_id": "synthetic-us", "module_id": "ultrasound", "module_version": "0.2.0", "parameters": {}, "protocol_id": data["protocol_id"], "input_artifacts": [source, frame]}
        result = RawUltrasoundProvider(frame_loader=load_frame).analyze(request, ProviderContext(self.store))
        summary = next(json.loads(self.store.read(r)) for r in result["artifacts"] if r["role"] == "derived" and r["media_type"] == "application/json")
        return result, summary

    def test_actual_pixel_decode_auto_frame_and_regional_units(self):
        result, summary = self.analyze()
        self.assertEqual(summary["frame_index"], 1)
        self.assertEqual(summary["frame_count"], 2)
        self.assertAlmostEqual(summary["calibration"]["mm_per_pixel_x"], .2)
        self.assertAlmostEqual(summary["calibration"]["mm_per_pixel_y"], .3)
        self.assertFalse(summary["import"]["pixel_spacing_used"])
        self.assertTrue(all(o["value"] is None for o in result["observations"]))

    def test_ambiguous_regions_remain_unknown_despite_global_spacing(self):
        self.ds.SequenceOfUltrasoundRegions.append(deepcopy(self.ds.SequenceOfUltrasoundRegions[0]))
        _, summary = self.analyze()
        self.assertEqual(summary["calibration"]["status"], "unknown")
        self.assertIsNone(summary["calibration"]["mm_per_pixel_x"])

    def test_external_scale_or_hidden_annotations_cannot_override_tags(self):
        data = deepcopy(self.data)
        data["calibration"].update(status="verified", source="external", author_id="test", mm_per_pixel_x=99, mm_per_pixel_y=99, region=[0, 0, 9, 9])
        with self.assertRaises(ContractError): self.analyze(data)
        data = deepcopy(self.data); data["calibration"]["roi"] = [1, 2, 3, 4]
        with self.assertRaises(ContractError): self.analyze(data)

    def test_non_us_modality_and_outside_frames_rejected(self):
        self.ds.Modality = "MR"
        with self.assertRaises(ContractError): self.analyze()
        self.ds.Modality = "US"; data = deepcopy(self.data); data["frame_index"] = 2
        with self.assertRaises(ContractError): self.analyze(data)


if __name__ == "__main__": unittest.main()
