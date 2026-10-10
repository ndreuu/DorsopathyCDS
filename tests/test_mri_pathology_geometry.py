"""Physical orientation and rejection tests; no inference or accuracy claims."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from dorsopathy_platform.contracts import ContractError


@unittest.skipUnless(importlib.util.find_spec("SimpleITK") and importlib.util.find_spec("numpy"), "imaging worker environment required")
class PathologyGeometryTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import SimpleITK as sitk
        self.np, self.sitk = np, sitk
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, array=None, spacing=(4., 1., 1.)):
        if array is None:
            array = self.np.arange(64 * 64 * 12, dtype=self.np.float32).reshape(64, 64, 12)
        image = self.sitk.GetImageFromArray(array)
        image.SetSpacing(spacing)
        path = self.root / "input.mha"
        self.sitk.WriteImage(image, str(path))
        return path, array

    def read(self, path, contrast="T2"):
        from dorsopathy_workers.mri_pathology.geometry import read_sagittal_volume
        run_dir = self.root / "run"
        run_dir.mkdir(exist_ok=True)
        return read_sagittal_volume(path, run_dir, contrast)

    def test_lossless_orientation_preserves_exact_voxels_and_physical_points(self):
        path, array = self.save()
        volume, scalar, image, info = self.read(path)
        self.np.testing.assert_array_equal(volume, array[::-1, :, ::-1])
        self.assertEqual(scalar, 1.)
        self.assertEqual(volume.shape, (64, 64, 12))
        self.assertEqual(image.TransformIndexToPhysicalPoint((0, 0, 0)), (44., 0., 63.))
        self.assertEqual(image.TransformIndexToPhysicalPoint((5, 7, 3)), (32., 5., 56.))
        self.assertFalse(info["interpolated"])
        self.assertEqual(info["pixel_spacing_rc_mm"], [1., 1.])

    def test_axial_acquisition_not_assessed_as_sagittal(self):
        path, _ = self.save(spacing=(1., 1., 4.))
        with self.assertRaises(ContractError):
            self.read(path)

    def test_anisotropic_pixels_are_not_averaged(self):
        path, _ = self.save(spacing=(4., 1., .8))
        with self.assertRaises(ContractError):
            self.read(path)

    def test_nonfinite_intensity_rejected(self):
        array = self.np.ones((64, 64, 12), dtype=self.np.float32)
        array[1, 1, 1] = self.np.nan
        path, _ = self.save(array)
        with self.assertRaises(ContractError):
            self.read(path)

    def test_constant_intensity_rejected(self):
        path, _ = self.save(self.np.ones((64, 64, 12), dtype=self.np.float32))
        with self.assertRaises(ContractError):
            self.read(path)

    def test_too_few_source_slices_rejected(self):
        path, _ = self.save(self.np.arange(64 * 64 * 8, dtype=self.np.float32).reshape(64, 64, 8))
        with self.assertRaises(ContractError):
            self.read(path)

    def test_external_mha_cannot_resolve_host_file(self):
        path = self.root / "external.mha"
        path.write_text("ObjectType = Image\nNDims = 3\nElementDataFile = forbidden.raw\n")
        with self.assertRaises(ContractError):
            self.read(path)

    def test_contrast_requires_explicit_t2_selection(self):
        path, _ = self.save()
        with self.assertRaises(ContractError):
            self.read(path, "T1")
