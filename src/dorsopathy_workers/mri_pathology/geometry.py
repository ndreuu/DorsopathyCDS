"""Strict single-file MRI import and lossless orientation for SpineNet.

This module belongs to the imaging worker, never the platform process.
SpineNet's code consumes a scalar in-plane spacing despite its type annotation.
Anisotropic in-plane grids are rejected rather than averaged or silently resized.
"""
from pathlib import Path
import math
import shutil


def read_sagittal_volume(source_path, run_dir, contrast="T2"):
    import numpy as np
    import SimpleITK as sitk
    from dorsopathy_platform.contracts import ContractError

    if contrast != "T2":
        raise ContractError("Pathology grading requires an explicitly selected T2 volume")
    source_path, run_dir = Path(source_path), Path(run_dir)
    with source_path.open("rb") as stream:
        header = stream.read(512)
    if b"ObjectType" in header or b"ElementDataFile" in header:
        suffix = ".mha"
        with source_path.open("rb") as stream:
            for _ in range(256):
                line = stream.readline(4096)
                if len(line) == 4096:
                    raise ContractError("MRI header line exceeds limit")
                if line.strip().lower().startswith(b"elementdatafile"):
                    if line.partition(b"=")[2].strip().upper() != b"LOCAL":
                        raise ContractError("Only embedded single-file MHA is supported")
                    break
            else:
                raise ContractError("MRI embedded-data header is missing")
    elif header.startswith(b"\x1f\x8b"):
        suffix = ".nii.gz"
    elif header[:4] in (b"\x5c\x01\x00\x00", b"\x00\x00\x01\x5c"):
        suffix = ".nii"
    else:
        raise ContractError("Pathology input must be one MHA or NIfTI volume")
    path = run_dir / ("source" + suffix)
    shutil.copyfile(source_path, path)
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.ReadImageInformation()
    if reader.GetDimension() != 3 or reader.GetNumberOfComponents() != 1:
        raise ContractError("Pathology input must be scalar 3D MRI")
    size, spacing = reader.GetSize(), np.asarray(reader.GetSpacing())
    direction = np.asarray(reader.GetDirection()).reshape(3, 3)
    origin = np.asarray(reader.GetOrigin())
    if not all(int(n) > 0 for n in size) or math.prod(size) > 40_000_000:
        raise ContractError("MRI volume exceeds the supported voxel limit")
    if not np.isfinite(spacing).all() or np.any(spacing <= 0) or not np.isfinite(origin).all():
        raise ContractError("MRI calibration is invalid")
    if not np.isfinite(direction).all() or not np.allclose(direction.T @ direction, np.eye(3), atol=1e-4, rtol=0):
        raise ContractError("MRI direction must be orthonormal")
    thick_axis = int(np.argmax(spacing))
    if spacing[thick_axis] >= 1.5 * np.partition(spacing, -2)[-2] and abs(direction[0, thick_axis]) < .95:
        raise ContractError("Acquisition geometry is not sagittal")
    reader.SetOutputPixelType(sitk.sitkFloat32)
    original = reader.Execute()
    # PIR: X pixels advance posterior, Y rows inferior, Z slices rightward.
    # DICOMOrient only permutes/flips existing voxels; no interpolation occurs.
    canonical = sitk.DICOMOrient(original, "PIR")
    canonical_direction = np.asarray(canonical.GetDirection()).reshape(3, 3)
    target = np.array([[0., 0., -1.], [1., 0., 0.], [0., -1., 0.]])
    if np.any(np.diag(target.T @ canonical_direction) < .95):
        raise ContractError("MRI axes are too oblique for this SpineNet input profile")
    canonical_spacing = canonical.GetSpacing()
    if abs(canonical_spacing[0] - canonical_spacing[1]) > 1e-4 * max(canonical_spacing[:2]):
        raise ContractError("SpineNet input requires isotropic in-plane pixel spacing")
    array = sitk.GetArrayFromImage(canonical)
    volume = np.ascontiguousarray(np.transpose(array, (1, 2, 0)), dtype=np.float32)
    if not 9 <= volume.shape[2] <= 128 or min(volume.shape[:2]) < 64:
        raise ContractError("MRI must have 9 to 128 sagittal slices and adequate frame dimensions")
    if not np.isfinite(volume).all() or np.ptp(volume) <= 0:
        raise ContractError("MRI intensities must be finite and nonconstant")
    def geometry(image):
        return {"size_xyz": list(image.GetSize()), "spacing_xyz_mm": list(image.GetSpacing()),
                "origin_lps_mm": list(image.GetOrigin()), "direction_lps": list(image.GetDirection())}
    return volume, float(canonical_spacing[1]), canonical, {
        "original_geometry": geometry(original), "canonical_geometry": geometry(canonical),
        "orientation": "PIR", "array_order": "height,width,sagittal_slices",
        "operation": "lossless_axis_permutation_and_flip", "interpolated": False,
        "pixel_spacing_rc_mm": [canonical_spacing[1], canonical_spacing[0]],
        "contrast": contrast, "contrast_source": "trusted_explicit_declaration",
        "slice_direction": "rightward", "numbering_status": "needs_confirmation",
    }
