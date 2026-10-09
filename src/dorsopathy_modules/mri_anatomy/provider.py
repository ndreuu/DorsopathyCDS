"""No imaging libraries here: validate planes, measure masks and render PNGs."""
import hashlib
import json
import math
import re
import struct
import zlib

from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope

_MAX_JSON = 8 * 1024 * 1024
_MAX_TOTAL_PIXELS = 4_000_000
_MAX_MEASUREMENTS = 64
_MAX_CONTOUR_SEGMENTS = 100_000
_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_CONCEPTS = {"spine.canal.cross_sectional_area": "mm2", "spine.canal.ap_extent": "mm"}


def _fail(message="Unsupported MRI source-grid data"):
    raise ContractError(message)


def _obj(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail("MRI source requires exactly documented fields")


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}: _fail("Invalid MRI opaque identifier")


def _finite(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def _vector(value, length):
    if type(value) is not list or len(value) != length or not all(_finite(v) for v in value): _fail("Invalid MRI geometry vector")
    return value


def _dot(a, b): return sum(x * y for x, y in zip(a, b))


def _unit_vectors(row, column):
    if abs(_dot(row, row) - 1) > 1e-4 or abs(_dot(column, column) - 1) > 1e-4 or abs(_dot(row, column)) > 1e-4:
        _fail("MRI plane directions must be orthonormal")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read(ref, context):
    if ref["media_type"] != "application/json" or ref["size_bytes"] > _MAX_JSON: _fail("MRI JSON exceeds the supported profile")
    with context.artifacts.open(ref) as stream: raw = stream.read(_MAX_JSON + 1)
    if len(raw) > _MAX_JSON: _fail("MRI JSON exceeds the supported profile")
    return loads_json(raw)


def _matrix(value, shape, mask=False):
    if type(value) is not list or len(value) != shape[0] or any(type(row) is not list or len(row) != shape[1] for row in value):
        _fail("MRI pixels do not match declared frame geometry")
    for row in value:
        for pixel in row:
            if mask:
                if type(pixel) is not int or pixel not in (0, 1): _fail("MRI measurement masks must be binary integer arrays")
            elif not _finite(pixel): _fail("MRI image pixels must be finite numbers")
    return value


def _png(array, rgb=False):
    height, width = len(array), len(array[0])
    raw = b"".join(b"\0" + bytes(component for pixel in row for component in (pixel if rgb else [pixel])) for row in array)
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2 if rgb else 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _largest_component(mask):
    remaining = {(r, c) for r, row in enumerate(mask) for c, value in enumerate(row) if value}
    components = []
    while remaining:
        first = remaining.pop(); stack = [first]; component = {first}
        while stack:
            r, c = stack.pop()
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    candidate = (r + dr, c + dc)
                    if candidate in remaining:
                        remaining.remove(candidate); component.add(candidate); stack.append(candidate)
        components.append(component)
    return max(components, key=lambda points: (len(points), tuple(sorted(points))), default=set()), len(components)


def _physical(geometry, row, column):
    return [geometry["origin_lps"][i] + row * geometry["pixel_spacing_rc_mm"][0] * geometry["row_direction_lps"][i]
            + column * geometry["pixel_spacing_rc_mm"][1] * geometry["column_direction_lps"][i] for i in range(3)]


def _boundary(points, geometry, limit=_MAX_CONTOUR_SEGMENTS):
    segments = []
    for r, c in sorted(points):
        for neighbor, start, end in (((r-1,c),(r-.5,c-.5),(r-.5,c+.5)),
                                      ((r+1,c),(r+.5,c-.5),(r+.5,c+.5)),
                                      ((r,c-1),(r-.5,c-.5),(r+.5,c-.5)),
                                      ((r,c+1),(r-.5,c+.5),(r+.5,c+.5))):
            if neighbor not in points:
                if len(segments) >= limit: _fail("MRI contours exceed the technical resource budget")
                segments.append({"points_rc": [list(start), list(end)], "points_lps_mm": [_physical(geometry, *start), _physical(geometry, *end)]})
    return segments


def _measurement_meta(measurement):
    _obj(measurement, {"measurement_id", "concept", "level", "numbering_status", "assessment_status", "quality", "method", "reason", "line"})
    _id(measurement["measurement_id"])
    if measurement["concept"] not in _CONCEPTS or measurement["level"] not in {"L1-L2", "L2-L3", "L3-L4", "L4-L5", "L5-S1"}: _fail()
    if measurement["numbering_status"] not in {"confirmed", "needs_confirmation"} or measurement["assessment_status"] not in {"assessed", "not_assessed"}: _fail()
    if measurement["quality"] not in {"usable", "unreliable", "unknown"} or type(measurement["method"]) is not str or not measurement["method"].strip(): _fail()
    if measurement["reason"] is not None and (type(measurement["reason"]) is not str or not measurement["reason"].strip()): _fail()


def analyze_source(data, request, context, source_ref, external_artifacts=()):
    """Shared measured-grid implementation used by upload and trusted bridges."""
    request = validate_request(request)
    _obj(data, {"schema_version", "source_kind", "mode", "processing", "acquired_at", "source", "registration_status",
                "model", "provenance", "comparison_metadata", "frames", "volume_measurements", "warnings"})
    if data["schema_version"] != "1.0" or data["source_kind"] != "mri.source-grid.v1": _fail()
    if data["mode"] not in ("synthetic_demo", "recorded") or data["processing"] not in ("fresh", "replay"): _fail()
    if data["registration_status"] not in ("confirmed", "needs_review", "unknown"): _fail()
    if type(data["provenance"]) is not dict or (data["comparison_metadata"] is not None and type(data["comparison_metadata"]) is not dict): _fail()
    refs = {ref["artifact_id"]: ref for ref in [*request["input_artifacts"], source_ref, *external_artifacts]}
    _obj(data["source"], {"format", "artifact_id", "sha256"})
    original_id = data["source"]["artifact_id"]
    if data["mode"] == "synthetic_demo":
        if data["source"] != {"format": "synthetic_grid", "artifact_id": None, "sha256": None} or data["model"] is not None: _fail("Synthetic MRI source must remain explicitly synthetic")
    else:
        if original_id not in refs or refs[original_id]["sha256"] != data["source"]["sha256"]: _fail("Recorded MRI requires an exact linked source artifact")
    model = data["model"]
    if model is not None:
        _obj(model, {"id", "version", "weights_sha256"}); _id(model["id"]); _id(model["version"])
        if type(model["weights_sha256"]) is not str or not _SHA.fullmatch(model["weights_sha256"]): _fail("MRI model checksum must be verified by its worker")
    if type(data["frames"]) is not list or not 1 <= len(data["frames"]) <= 64 or type(data["volume_measurements"]) is not list: _fail()
    if type(data["warnings"]) is not list or not all(type(w) is str and w.strip() for w in data["warnings"]): _fail()
    # Preflight aggregate dimensions and requests before opening arrays or rendering.
    missing_levels=data["provenance"].get("unassessed_levels",[])
    if type(missing_levels)is not list: _fail("Invalid missing MRI level provenance")
    pixel_count, measurement_count = 0, len(data["volume_measurements"])+len(missing_levels)
    for frame in data["frames"]:
        if type(frame)is not dict or type(frame.get("geometry"))is not dict: _fail()
        shape=frame["geometry"].get("shape_rc")
        if type(shape)is not list or len(shape)!=2 or any(type(n)is not int or not 1<=n<=2048 for n in shape) or math.prod(shape)>1_000_000: _fail("Unsupported MRI frame dimensions")
        if type(frame.get("measurements"))is not list: _fail()
        pixel_count+=math.prod(shape); measurement_count+=len(frame["measurements"])
    if pixel_count>_MAX_TOTAL_PIXELS: _fail("MRI frames exceed the aggregate pixel budget")
    if measurement_count>_MAX_MEASUREMENTS: _fail("MRI requests exceed the measurement budget")
    origin = "synthetic_demo" if data["mode"] == "synthetic_demo" else "automatically_measured"
    observations, measurements, frames, artifact_list = [], [], [], list(refs.values())
    seen_frames, seen_measurements = set(), set()
    contour_count = 0
    warnings = [{"code": "anatomy_only", "message": "Anatomical masks and geometric measurements are not herniation, stenosis grade, conus or dural-sac assessment."}]
    warnings += [{"code": "source_warning", "message": warning} for warning in data["warnings"]]
    if data["registration_status"] != "confirmed": warnings.append({"code": "registration_not_confirmed", "message": "Cross-series registration has not been confirmed."})
    for frame_position,frame in enumerate(data["frames"]):
        _obj(frame, {"frame_index", "frame_kind", "geometry", "image", "mask", "image_artifact_id", "mask_artifact_id", "mask_kind", "structure", "measurements"})
        index = frame["frame_index"]
        if type(index) is not int or index < 0 or index in seen_frames: _fail("Invalid or duplicate MRI frame index")
        seen_frames.add(index)
        if type(frame["frame_kind"]) is not str or not frame["frame_kind"].strip() or frame["mask_kind"] != "anatomy" or frame["structure"] not in ("spinal_canal","intervertebral_disc"): _fail("Only explicitly anatomical canal and disc plane masks are supported")
        geometry = frame["geometry"]
        _obj(geometry, {"shape_rc", "pixel_spacing_rc_mm", "origin_lps", "row_direction_lps", "column_direction_lps"})
        shape = geometry["shape_rc"]
        if type(shape) is not list or len(shape) != 2 or any(type(n) is not int or not 1 <= n <= 2048 for n in shape) or shape[0]*shape[1] > 1000000: _fail("Unsupported MRI frame dimensions")
        spacing = _vector(geometry["pixel_spacing_rc_mm"], 2)
        if any(s <= 0 for s in spacing): _fail("MRI pixel spacing must be positive")
        _vector(geometry["origin_lps"], 3)
        row_direction = _vector(geometry["row_direction_lps"], 3); column_direction = _vector(geometry["column_direction_lps"], 3)
        _unit_vectors(row_direction, column_direction)
        arrays = []
        for key in ("image", "mask"):
            reference_id = frame[key + "_artifact_id"]
            if reference_id is None: value = frame[key]
            else:
                if frame[key] is not None or reference_id not in refs: _fail("MRI frame reference is missing or ambiguous")
                value = _read(refs[reference_id], context)
            arrays.append(_matrix(value, shape, mask=key == "mask"))
        image, mask = arrays
        if type(frame["measurements"]) is not list or (not frame["measurements"] and frame["structure"]!="intervertebral_disc"): _fail("MRI canal frame requires an explicit measurement request")
        if frame["structure"]=="intervertebral_disc" and frame["measurements"]: _fail("Disc planes are display evidence; disc volume is measured on the full 3D mask")
        points, component_count = _largest_component(mask)
        if frame["structure"]=="intervertebral_disc" or any(m.get("method") == "spinosarc-native-plane-all-mask-pixels" for m in frame["measurements"] if type(m) is dict):
            points = {(r,c) for r,row in enumerate(mask) for c,pixel in enumerate(row) if pixel}
        contour_segments = _boundary(points, geometry) if frame["measurements"] else []
        contour_count += len(contour_segments)*len(frame["measurements"])
        if contour_count>_MAX_CONTOUR_SEGMENTS: _fail("MRI contours exceed the aggregate evidence budget")
        selected = [[int((r,c) in points) for c in range(shape[1])] for r in range(shape[0])]
        low = min(min(row) for row in image); high = max(max(row) for row in image)
        gray = [[round((value-low)*255/(high-low)) if high > low else 0 for value in row] for row in image]
        overlay = [[[min(255, round(value*.5+127)), round(value*.5), round(value*.5)] if selected[r][c] else [value,value,value] for c,value in enumerate(row)] for r,row in enumerate(gray)]
        frame_refs = {}
        for name, content in (("image",_png(gray)), ("mask",_png([[pixel*255 for pixel in row] for row in selected])), ("overlay",_png(overlay,True))):
            ref = context.artifacts.add_bytes(content, "image/png", "derived"); artifact_list.append(ref); frame_refs[name + "_artifact_id"] = ref["artifact_id"]
        frame_sidecar = {"frame_index": index, "frame_kind": frame["frame_kind"], "geometry": geometry,
                         "coordinate_frame": "DICOM-LPS-mm", "mask_kind": "anatomy", "structure": frame["structure"], **frame_refs}
        frames.append(frame_sidecar)
        for measurement_position,measurement in enumerate(frame["measurements"]):
            _measurement_meta(measurement)
            measurement_id = measurement["measurement_id"]
            if measurement_id in seen_measurements: _fail("Duplicate MRI measurement identifier")
            seen_measurements.add(measurement_id)
            assessed = measurement["assessment_status"] == "assessed"
            reason = measurement["reason"]
            value, line_endpoints = None, None
            if assessed and not points: assessed, reason = False, "empty_canal_mask"
            if assessed and any(r in (0,shape[0]-1) or c in (0,shape[1]-1) for r,c in points): assessed, reason = False, "mask_touches_frame_boundary"
            if measurement["concept"] == "spine.canal.cross_sectional_area":
                if measurement["line"] is not None: _fail("Area measurements must not declare a line")
                if assessed: value = len(points)*spacing[0]*spacing[1]
            else:
                line = measurement["line"]
                if line is None:
                    if assessed: _fail("AP extent requires an explicit source-grid line")
                else:
                    _obj(line, {"row", "column_first", "column_last"})
                    r, first, last = line["row"], line["column_first"], line["column_last"]
                    if any(type(n) is not int for n in (r,first,last)) or not 0 <= r < shape[0] or not 0 <= first <= last < shape[1]: _fail("Invalid MRI AP line")
                    if any(abs(column_direction[i] - (1 if i == 1 else 0)) > 1e-4 for i in (0,2)) or abs(abs(column_direction[1])-1)>1e-4: _fail("AP extent must follow the patient AP axis")
                    if assessed and any((r,c) not in points for c in range(first,last+1)): _fail("MRI AP line leaves the measured canal component")
                    if assessed:
                        value = (last-first+1)*spacing[1]
                        line_endpoints = [_physical(geometry,r,first-.5),_physical(geometry,r,last+.5)]
            measurements.append({**measurement, "assessment_status": "assessed" if assessed else "not_assessed",
                "value": value, "unit": _CONCEPTS[measurement["concept"]], "frame_index": index, "reason": reason,
                "source_pointer": "/frames/"+str(frame_position)+"/measurements/"+str(measurement_position),
                "selected_component_voxel_count": len(points), "component_count": component_count,
                "contour_segments": contour_segments, "line_endpoints_lps_mm": line_endpoints})
    for item_position,item in enumerate(data["volume_measurements"]):
        required={"measurement_id", "level", "numbering_status", "geometry", "voxel_indices_xyz", "mask_artifact_id", "method"}
        if type(item)is not dict or not required<=set(item) or set(item)-required-{"display_frame_index"}: _fail("Invalid disc-volume profile")
        _id(item["measurement_id"])
        if item["measurement_id"] in seen_measurements or item["level"] not in {"L1-L2","L2-L3","L3-L4","L4-L5","L5-S1"} or item["numbering_status"] not in {"confirmed","needs_confirmation"}: _fail()
        seen_measurements.add(item["measurement_id"])
        geometry = item["geometry"]
        _obj(geometry, {"shape_xyz","spacing_xyz_mm","origin_lps","direction"})
        shape = geometry["shape_xyz"]; spacing = _vector(geometry["spacing_xyz_mm"],3)
        if type(shape) is not list or len(shape)!=3 or any(type(n)is not int or n<=0 for n in shape) or any(s<=0 for s in spacing): _fail()
        _vector(geometry["origin_lps"],3); direction = _vector(geometry["direction"],9)
        cols = [[direction[r*3+c] for r in range(3)] for c in range(3)]
        for c in range(3):
            if abs(_dot(cols[c],cols[c])-1)>1e-4 or any(abs(_dot(cols[c],cols[d]))>1e-4 for d in range(c)): _fail("Invalid MRI volume direction")
        indices = item["voxel_indices_xyz"]
        if type(indices)is not list or len(indices)>1000000: _fail()
        seen_voxels=set()
        for point in indices:
            if type(point)is not list or len(point)!=3 or any(type(v)is not int or not 0<=v<shape[a] for a,v in enumerate(point)): _fail("Invalid disc-mask voxel index")
            identity=tuple(point)
            if identity in seen_voxels: _fail("Duplicate disc-mask voxel index")
            seen_voxels.add(identity)
        if item["mask_artifact_id"] not in refs or type(item["method"])is not str or not item["method"].strip(): _fail("Disc volume requires a linked source-grid mask")
        display_index=item.get("display_frame_index")
        if display_index is not None and (type(display_index)is not int or display_index not in seen_frames): _fail("Disc-volume display frame is unavailable")
        measurements.append({"measurement_id":item["measurement_id"],"concept":"spine.disc.mask_volume","level":item["level"],
            "numbering_status":item["numbering_status"],"assessment_status":"assessed" if indices else "not_assessed",
            "quality":"usable" if indices else "unknown","method":item["method"],"reason":None if indices else "disc_label_absent",
            "value":len(indices)*math.prod(spacing) if indices else None,"unit":"mm3","frame_index":display_index,
            "source_pointer":"/volume_measurements/"+str(item_position),
            "mask_artifact_id":item["mask_artifact_id"],"voxel_count":len(indices),"geometry":geometry,
            "whole_disc_volume_validated":False,"mask_kind":"anatomy"})
    for position,item in enumerate(missing_levels):
        _obj(item,{"level","reason"})
        if item["level"] not in {"L1-L2","L2-L3","L3-L4","L4-L5","L5-S1"} or type(item["reason"])is not str or not item["reason"].strip(): _fail()
        measurements.append({"measurement_id":"unassessed-canal-"+item["level"],"concept":"spine.canal.cross_sectional_area",
            "level":item["level"],"numbering_status":"needs_confirmation","assessment_status":"not_assessed","quality":"unknown",
            "method":"native-plane-coverage-check","reason":item["reason"],"value":None,"unit":"mm2","frame_index":None,
            "source_pointer":"/provenance/unassessed_levels/"+str(position)})
    if not measurements: _fail("MRI source contains no measurable or explicitly unassessed anatomy")
    sidecar = {"schema_version":"1.0","source_kind":"mri.measurements.v1","acquisition":"synthetic" if origin=="synthetic_demo" else "recorded",
        "processing":data["processing"],"origin":origin,"acquired_at":data["acquired_at"],"source":data["source"],
        "registration_status":data["registration_status"],"comparison_metadata":data["comparison_metadata"],
        "model":model,"provenance":data["provenance"],"frames":frames,"measurements":measurements,"unassessed_structures":["disc_herniation","stenosis_grade","dural_sac","conus","root_compression"]}
    sidecar_ref = context.artifacts.add_bytes(_canonical(sidecar),"application/json","derived"); artifact_list.append(sidecar_ref)
    for measurement in measurements:
        index=measurement["frame_index"]
        evidence=[{"artifact_id":source_ref["artifact_id"],"selector":{"kind":"json","pointer":measurement["source_pointer"]}},
                  {"artifact_id":sidecar_ref["artifact_id"],"selector":{"kind":"measurement","measurement_id":measurement["measurement_id"],"frame_index":index}}]
        if index is not None:
            frame=next(f for f in frames if f["frame_index"]==index)
            for key in ("image_artifact_id","mask_artifact_id","overlay_artifact_id"):
                evidence.append({"artifact_id":frame[key],"selector":{"kind":"image","frame_index":index,"coordinate_frame":"DICOM-LPS-mm"}})
        if "mask_artifact_id" in measurement:
            evidence.append({"artifact_id":measurement["mask_artifact_id"],"selector":{"kind":"volume_mask","mask_kind":"anatomy"}})
        observations.append({"observation_id":observation_id_for(request["job_id"],measurement["concept"],measurement["measurement_id"]),
            "concept":measurement["concept"],"assessment_status":measurement["assessment_status"],"value":measurement["value"],"unit":measurement["unit"],
            "anatomy":{"region":"lumbar_spine","side":"midline","level":measurement["level"],"numbering_status":measurement["numbering_status"]},
            "quality":measurement["quality"] if measurement["assessment_status"]=="assessed" else "unknown","origin":origin,
            "method":measurement["method"],"acquired_at":data["acquired_at"],"coordinate_frame":"DICOM-LPS-mm","evidence_refs":evidence,"model":model})
    artifacts={ref["artifact_id"]:ref for ref in artifact_list}
    status="partial" if any(o["assessment_status"]=="not_assessed" for o in observations) else "complete"
    return validate_result(result_envelope(request,observations,list(artifacts.values()),status,warnings),request)


class MRIAnatomyProvider:
    spec=ProviderSpec("mri.anatomy","0.1.0","МРТ: анатомия и измерения")

    def __init__(self, live_provider=None):
        self.live_provider = live_provider

    def analyze(self, request, context):
        request=validate_request(request)
        if (request["module_id"],request["module_version"])!=(self.spec.module_id,self.spec.module_version) or set(request["parameters"])-{"processing_mode"} or not request["input_artifacts"]: _fail("MRI provider requires its documented version and processing_mode only")
        mode = request["parameters"].get("processing_mode", "replay")
        if mode not in ("replay", "live"): _fail("Unsupported MRI processing mode")
        if mode == "live":
            if self.live_provider is None: _fail("MRI live worker is not configured")
            return self.live_provider.analyze(request, context)
        source=request["input_artifacts"][0]
        if source["role"]!="source": _fail("MRI replay requires its source-grid JSON source artifact first")
        data = _read(source, context)
        if type(data) is dict and data.get("model") is not None:
            _fail("Uploaded replay cannot assert independently verified model weights")
        return analyze_source(data,request,context,source)

    def close(self):
        if self.live_provider is not None:
            self.live_provider.close()
