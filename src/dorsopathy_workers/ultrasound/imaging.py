"""Pydicom frame decoding with regional calibration; no global spacing guess."""
import math
from dorsopathy_platform.contracts import ContractError
from dorsopathy_modules.ultrasound.provider import calibration_from_dicom_regions
from dorsopathy_modules.ultrasound_raw.provider import load_png, MAX_PIXELS, validate_calibration


def _display(array, photometric):
    import numpy as np
    if not np.isfinite(array).all():
        raise ContractError("DICOM frame contains nonfinite pixels")
    if photometric == "MONOCHROME2" and array.ndim == 2:
        lo, hi = float(array.min()), float(array.max())
        image = np.rint((array.astype(np.float32) - lo) * (255 / max(hi - lo, 1))).astype(np.uint8)
        image = np.repeat(image[:, :, None], 3, axis=2)
        transform = {"kind": "linear_minmax_to_uint8", "source_min": lo, "source_max": hi}
    elif photometric == "RGB" and array.ndim == 3 and array.shape[-1] == 3 and array.dtype == np.uint8:
        image = array
        transform = {"kind": "identity_rgb_uint8"}
    else:
        raise ContractError("Initial DICOM profile supports MONOCHROME2 or 8-bit RGB only")
    return image, transform


def load_frame(frame, data, context):
    if frame["media_type"] == "image/png":
        return load_png(frame, data, context)
    import numpy as np
    import pydicom
    from pydicom.pixels import pixel_array
    with context.artifacts.open(frame) as stream:
        header = pydicom.dcmread(stream, stop_before_pixels=True)
    if str(getattr(header, "Modality", "")) != "US":
        raise ContractError("Raw ultrasound DICOM requires US modality")
    width, height = int(header.Columns), int(header.Rows)
    count = int(getattr(header, "NumberOfFrames", 1))
    if not 0 < width <= 2048 or not 0 < height <= 2048 or width * height > MAX_PIXELS or not 1 <= count <= 256:
        raise ContractError("DICOM frame dimensions or count exceed limits")
    # Manifest metric declarations cannot silently override DICOM regions.
    declared_cal = validate_calibration(data["calibration"], width, height)
    if declared_cal["status"] != "unknown":
        raise ContractError("DICOM raw profile requires unknown manifest scale; regional tags supply calibration")
    calibration_reason = None
    try:
        cal = calibration_from_dicom_regions(list(getattr(header, "SequenceOfUltrasoundRegions", [])), width, height)
    except ContractError:
        cal = {"status": "unknown", "source": "DICOM-US-Region-Calibration-unusable", "author_id": "dicom-header",
            "mm_per_pixel_x": None, "mm_per_pixel_y": None, "region": None}
        calibration_reason = "missing_ambiguous_or_unsupported_us_region_tags"
    photometric = str(getattr(header, "PhotometricInterpretation", ""))
    wanted = data["frame_index"]
    if wanted is not None and wanted >= count:
        raise ContractError("Requested DICOM frame is outside NumberOfFrames")
    candidates = [wanted] if wanted is not None else sorted(set(round(i * (count - 1) / min(count - 1, 15)) for i in range(min(count, 16)))) if count > 1 else [0]
    ranking = []; best = None
    for index in candidates:
        with context.artifacts.open(frame) as stream:
            array = pixel_array(stream, index=index)
        image, transform = _display(array, photometric)
        if image.shape != (height, width, 3):
            raise ContractError("Decoded DICOM dimensions differ from header")
        x0, y0, x1, y1 = cal["region"] or [0, 0, width - 1, height - 1]
        tissue = image[y0:y1 + 1, x0:x1 + 1].mean(axis=2)
        contrast = float(np.percentile(tissue, 90) - np.percentile(tissue, 10))
        ranking.append({"frame_index": index, "robust_intensity_range": contrast})
        if best is None or contrast > best[0]:
            best = (contrast, index, image, transform)
    _, index, image, transform = best
    return {"width": width, "height": height, "rows": [bytearray(row.tobytes()) for row in image],
        "frame_index": index, "frame_count": count, "calibration": cal,
        "import": {"format": "DICOM", "decoder": "pydicom.pixels.pixel_array", "decoder_version": pydicom.__version__,
            "photometric_interpretation": photometric, "transfer_syntax_uid": str(header.file_meta.TransferSyntaxUID),
            "transform": transform, "selection": "declared_frame" if wanted is not None else "maximum_robust_contrast_up_to_16_uniform_frames_v1",
            "frame_qc": ranking, "frame_suitability": "technical_contrast_only_anatomical_plane_not_verified",
            "calibration_reason": calibration_reason, "pixel_spacing_used": False,
            "sop_instance_uid_preserved_in_source_only": True, "acquisition_time_from_header": None}}
