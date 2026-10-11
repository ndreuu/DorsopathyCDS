"""Raw image -> trusted automatic segmenter -> conservative calibrated metrics.

Neither masks, points, polygons nor host model paths are accepted in uploads.
The optional loader and segmenter are supplied by a trusted isolated worker.
"""
from copy import deepcopy
import json
import math
from statistics import median

from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_modules.ultrasound._png import decode_png
from dorsopathy_modules.ultrasound._support import common, png, line

FIELDS = {"schema_version", "source_kind", "acquisition_mode", "processing_mode", "origin",
    "subject_ref", "session_ref", "protocol_id", "acquired_at", "clock_domain", "pose",
    "coordinate_frame", "axes", "anatomy", "calibration", "frame_artifact_id", "frame_index",
    "structure", "plane"}
CAL_FIELDS = {"status", "source", "author_id", "mm_per_pixel_x", "mm_per_pixel_y", "region"}
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_DICOM_BYTES = 256 * 1024 * 1024
MAX_PIXELS = 2_100_000
CONCEPTS = (("ultrasound.muscle.cross_sectional_area", "mm2"), ("ultrasound.muscle.thickness", "mm"))


def _json(context, value):
    return context.artifacts.add_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode(), "application/json", role="derived")


def _finite(value, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or positive and value <= 0:
        raise ContractError("Ultrasound calibration requires finite positive scales")
    return float(value)


def validate_calibration(cal, width, height):
    if type(cal) is not dict or set(cal) != CAL_FIELDS or cal["status"] not in {"verified", "unknown", "invalid"}:
        raise ContractError("Unsupported raw ultrasound calibration declaration")
    for key in ("source", "author_id"):
        if cal[key] is not None and (type(cal[key]) is not str or not cal[key].strip()):
            raise ContractError("Calibration provenance must be nonempty")
    for key in ("mm_per_pixel_x", "mm_per_pixel_y"):
        if cal[key] is not None:
            _finite(cal[key], True)
    region = cal["region"]
    if region is not None:
        if type(region) is not list or len(region) != 4 or any(type(x) is not int for x in region):
            raise ContractError("Calibration bounds require four integer source pixel centres")
        if not 0 <= region[0] <= region[2] < width or not 0 <= region[1] <= region[3] < height:
            raise ContractError("Calibration region is outside the source image")
    if cal["status"] == "verified" and any(cal[k] is None for k in CAL_FIELDS - {"status"}):
        raise ContractError("Verified calibration requires scales, bounds and provenance")
    return deepcopy(cal)


def load_png(frame, data, context):
    """Bounded stdlib import; DICOM is decoded only by the optional worker."""
    if frame["media_type"] != "image/png":
        raise ContractError("DICOM decoding requires the isolated ultrasound worker")
    if data["frame_index"] not in (None, 0):
        raise ContractError("PNG has only frame zero")
    with context.artifacts.open(frame) as stream:
        raw = stream.read(MAX_PNG_BYTES + 1)
    if len(raw) > MAX_PNG_BYTES:
        raise ContractError("Raw ultrasound PNG exceeds the byte limit")
    width, height, rows = decode_png(raw)
    if width * height > MAX_PIXELS:
        raise ContractError("Raw ultrasound frame exceeds the pixel limit")
    return {"width": width, "height": height, "rows": rows, "frame_index": 0,
        "frame_count": 1, "calibration": validate_calibration(data["calibration"], width, height),
        "import": {"format": "PNG", "transform": "identity", "selection": "single_frame",
            "pixel_spacing_used": False, "acquisition_time_from_header": None}}


def mask_metrics(mask, width, height, calibration, plane):
    """Pixel-cell area and median vertical extent through central mask columns.

    Thickness is a declared image-axis extent, not a validated fascial thickness.
    Noncontiguous central columns, disconnected masks and clipping block metrics.
    """
    if type(mask) is not list or len(mask) != height or any(type(row) is not list or len(row) != width for row in mask):
        raise ContractError("Automatic mask must match original frame dimensions")
    if any(type(v) not in (bool, int) or v not in (0, 1) for row in mask for v in row):
        raise ContractError("Automatic mask must be binary")
    points = [(x, y) for y, row in enumerate(mask) for x, v in enumerate(row) if v]
    result = {"area_mm2": None, "thickness_mm": None, "area_reason": None,
        "thickness_reason": None, "area_pixels": len(points), "quality_flags": [],
        "thickness_segments_xy": [], "thickness_definition": "median_vertical_pixel_cell_extent_central_20_percent_width_v1"}
    if not points:
        result.update(area_reason="no_predicted_mask", thickness_reason="no_predicted_mask")
        return result
    selected = set(points)
    reached = {points[0]}; queue = [points[0]]
    for x, y in queue:
        for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if p in selected and p not in reached:
                reached.add(p); queue.append(p)
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    if len(reached) != len(points):
        result["quality_flags"].append("disconnected_mask")
    if min(xs) == 0 or max(xs) == width - 1 or min(ys) == 0 or max(ys) == height - 1:
        result["quality_flags"].append("mask_touches_source_boundary")
    cal = validate_calibration(calibration, width, height)
    if cal["status"] != "verified":
        result["quality_flags"].append("unknown_or_invalid_calibration")
    else:
        x0, y0, x1, y1 = cal["region"]
        if any(not x0 <= x <= x1 or not y0 <= y <= y1 for x, y in points):
            result["quality_flags"].append("mask_outside_calibration_region")
        elif any(x in (x0, x1) or y in (y0, y1) for x, y in points):
            result["quality_flags"].append("mask_touches_calibration_boundary")
    block = result["quality_flags"]
    if block:
        result.update(area_reason=block[0], thickness_reason=block[0])
        return result
    if plane == "transverse":
        result["area_mm2"] = len(points) * cal["mm_per_pixel_x"] * cal["mm_per_pixel_y"]
    else:
        result["area_reason"] = "transverse_acquisition_plane_required"
    # Central band is automatic and image-axis based. It is not a manual ROI.
    centre = (min(xs) + max(xs)) / 2
    half = max(0.5, (max(xs) - min(xs) + 1) * 0.1)
    extents = []
    for x in range(min(xs), max(xs) + 1):
        if abs(x - centre) > half:
            continue
        column = [y for y in range(height) if mask[y][x]]
        if not column:
            continue
        if column[-1] - column[0] + 1 != len(column):
            result["thickness_reason"] = "noncontiguous_central_column"
            return result
        extents.append(len(column) * cal["mm_per_pixel_y"])
        result["thickness_segments_xy"].append([[x, column[0] - .5], [x, column[-1] + .5]])
    if plane in {"transverse", "longitudinal"} and extents:
        result["thickness_mm"] = median(extents)
        result["thickness_segment_xy"] = min(result["thickness_segments_xy"], key=lambda p: abs((p[1][1] - p[0][1]) * cal["mm_per_pixel_y"] - result["thickness_mm"]))
    else:
        result["thickness_reason"] = "known_acquisition_plane_required"
    return result


class RawUltrasoundProvider:
    spec = ProviderSpec("ultrasound", "0.2.0", "Raw lumbar ultrasound with automatic model segmentation")

    def __init__(self, segmenter=None, frame_loader=None):
        self.segmenter = segmenter
        self.frame_loader = frame_loader or load_png

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != ("ultrasound", "0.2.0") or request["parameters"]:
            raise ContractError("Raw ultrasound requires its exact version and empty parameters")
        refs = request["input_artifacts"]
        manifests = [r for r in refs if r["role"] == "source" and r["media_type"] == "application/json"]
        if len(refs) != 2 or len(manifests) != 1 or manifests[0]["size_bytes"] > MAX_MANIFEST_BYTES:
            raise ContractError("Raw ultrasound requires one small manifest and one raw source")
        source = manifests[0]
        with context.artifacts.open(source) as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ContractError("Raw ultrasound manifest exceeds byte limit")
        data = loads_json(raw)
        if type(data) is not dict or set(data) != FIELDS:
            raise ContractError("Raw ultrasound manifest accepts only documented acquisition fields")
        common(data, "ultrasound-raw.v1", 2)
        if data["structure"] != "lumbar_multifidus" or data["anatomy"]["region"] != "lumbar_multifidus":
            raise ContractError("Raw ultrasound currently supports lumbar multifidus only")
        if data["anatomy"]["side"] == "bilateral":
            raise ContractError("A single top-ranked muscle mask cannot be assigned bilateral anatomy")
        if data["plane"] not in {"transverse", "longitudinal", "unknown"}:
            raise ContractError("Raw ultrasound requires a documented acquisition plane")
        if data["frame_index"] is not None and (type(data["frame_index"]) is not int or data["frame_index"] < 0):
            raise ContractError("Raw ultrasound frame index must be null or nonnegative integer")
        frame = next((r for r in refs if r["artifact_id"] == data["frame_artifact_id"]), None)
        if frame is None or frame == source or frame["role"] != "source" or frame["media_type"] not in {"image/png", "application/dicom"}:
            raise ContractError("Manifest must reference its uploaded raw PNG or DICOM")
        if frame["size_bytes"] > (MAX_PNG_BYTES if frame["media_type"] == "image/png" else MAX_DICOM_BYTES):
            raise ContractError("Raw ultrasound source exceeds supported byte limit")
        decoded = self.frame_loader(frame, data, context)
        width, height, rows = decoded["width"], decoded["height"], decoded["rows"]
        cal = validate_calibration(decoded["calibration"], width, height)
        source_png = context.artifacts.add_bytes(png(width, height, rows), "image/png", role="derived")
        artifacts = [source, frame, source_png]
        values = {"area_mm2": None, "thickness_mm": None, "area_reason": "model_not_configured",
            "thickness_reason": "model_not_configured", "quality_flags": [], "thickness_segments_xy": []}
        model = None; segmentation = {"status": "not_run", "reason": "model_not_configured"}
        image_range = max(max(row) for row in rows) - min(min(row) for row in rows)
        if image_range == 0:
            values.update(area_reason="constant_source_frame", thickness_reason="constant_source_frame")
            segmentation = {"status": "not_run", "reason": "constant_source_frame"}
        elif self.segmenter is not None:
            prediction = self.segmenter(decoded, "multifidus_muscle")
            model = prediction.get("model")
            segmentation = {k: v for k, v in prediction.items() if k != "mask"}
            mask = prediction.get("mask")
            if mask is not None:
                if model is None:
                    raise ContractError("Automatic segmentation requires actual model provenance")
                values = mask_metrics(mask, width, height, cal, data["plane"])
                score = prediction.get("score")
                if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ContractError("Automatic mask score is invalid")
                if score < .5:
                    values.update(area_mm2=None, thickness_mm=None, area_reason="low_model_score", thickness_reason="low_model_score")
                mask_rows = [bytearray(v for bit in row for v in ((255 if bit else 0),) * 3) for row in mask]
                mask_ref = context.artifacts.add_bytes(png(width, height, mask_rows), "image/png", role="derived")
                overlay_rows = [bytearray(row) for row in rows]
                for y in range(height):
                    for x in range(width):
                        if mask[y][x]:
                            for c, colour in enumerate((255, 210, 0)):
                                overlay_rows[y][x * 3 + c] = round(.55 * rows[y][x * 3 + c] + .45 * colour)
                if values.get("thickness_mm") is not None:
                    line(overlay_rows, *values["thickness_segment_xy"], color=(0, 255, 255))
                overlay_ref = context.artifacts.add_bytes(png(width, height, overlay_rows), "image/png", role="derived")
                artifacts += [mask_ref, overlay_ref]
                segmentation.update(mask_artifact_id=mask_ref["artifact_id"], overlay_artifact_id=overlay_ref["artifact_id"])
            else:
                reason = prediction.get("reason", "no_predicted_mask")
                values.update(area_reason=reason, thickness_reason=reason)
        summary = {"schema_version": "ultrasound.raw.analysis.v1", "processing": "fresh",
            "source_artifact_id": frame["artifact_id"], "manifest_artifact_id": source["artifact_id"],
            "source_png_artifact_id": source_png["artifact_id"], "width": width, "height": height,
            "frame_index": decoded["frame_index"], "frame_count": decoded["frame_count"],
            "acquired_at": data["acquired_at"], "pose": data["pose"], "plane": data["plane"],
            "plane_verification": "declared_acquisition_metadata_no_automatic_plane_classifier",
            "anatomy": data["anatomy"], "calibration": cal, "import": decoded["import"],
            "segmentation": segmentation, "measurements": values,
            "diagnostic_interpretation": "none", "contraction_change": "not_assessed_single_selected_frame",
            "comparison_metadata": {"schema_version": "1.0", "subject_ref": data["subject_ref"],
                "session_ref": data["session_ref"], "protocol_id": data["protocol_id"], "linkage_evidence_refs": [],
                "acquired_interval": {"start": data["acquired_at"], "end": data["acquired_at"], "clock_domain": data["clock_domain"]},
                "event_id": None, "pose_id": data["pose"], "calibration_status": cal["status"],
                "calibration_evidence_refs": [{"artifact_id": frame["artifact_id"] if frame["media_type"] == "application/dicom" else source["artifact_id"], "selector": {"kind": "calibration", "region": cal["region"]}}] if cal["status"] == "verified" else [],
                "frame": {"id": data["coordinate_frame"], "units": "px", "axes": data["axes"]}, "transform_refs": []}}
        sidecar = _json(context, summary); artifacts.append(sidecar)
        observations = []
        for (concept, unit), key in zip(CONCEPTS, ("area_mm2", "thickness_mm")):
            value = values[key]
            observations.append({"observation_id": observation_id_for(request["job_id"], concept, data["structure"]),
                "concept": concept, "assessment_status": "assessed" if value is not None else "not_assessed",
                "value": value, "unit": unit, "anatomy": data["anatomy"], "quality": "unknown" if cal["status"] != "invalid" else "unreliable",
                "origin": "synthetic_demo" if data["origin"] == "synthetic_demo" else "model_predicted" if model else "automatically_measured",
                "method": "automatic_model_pixel_cell_area_v1" if unit == "mm2" else "automatic_model_median_vertical_extent_v1",
                "evidence_refs": [{"artifact_id": frame["artifact_id"], "selector": {"kind": "image", "frame_index": decoded["frame_index"], "coordinate_frame": data["coordinate_frame"]}},
                    {"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/calibration"}},
                    {"artifact_id": sidecar["artifact_id"], "selector": {"kind": "json", "pointer": "/measurements/" + key}}],
                "model": model, "acquired_at": data["acquired_at"], "coordinate_frame": data["coordinate_frame"]})
        partial = any(o["value"] is None for o in observations)
        warnings = [{"code": "ultrasound_raw_limitations", "message": "Model mask measurements require acquisition-plane review; thickness is an image-axis extent. No diagnosis or contraction change is inferred."}]
        if partial:
            warnings.append({"code": "ultrasound_raw_unassessed", "message": "One or more raw ultrasound features remain unassessed; inspect the analysis evidence for the exact reason."})
        # Content-addressed previews may equal a source or each other. Preserve
        # the original reference metadata for shared identities.
        unique = {}
        for ref in artifacts:
            unique.setdefault(ref["artifact_id"], ref)
        return validate_result(result_envelope(request, observations, list(unique.values()), "partial" if partial else "complete", warnings), request)
