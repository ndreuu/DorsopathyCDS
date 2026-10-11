"""Original PNG16 + camera metadata -> automatic component/cloud/geometry."""
from datetime import datetime
import csv
import json
import math
from pathlib import Path
import struct
import tempfile
import zlib

from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.results import observation_id_for, result_envelope
from .depth_png import MAX_BYTES, decode_depth_png
from .geometry import camera_from_bytes, cloud_geometry, exact, finite, largest_component, project_pixel
from .provider import RAW_LIDAR_SPEC

COMMON = {
    "schema_version", "source_kind", "acquisition_mode", "processing_mode", "origin",
    "subject_ref", "session_ref", "protocol_id", "acquired_at", "clock_domain",
    "pose", "coordinate_frame", "axes", "anatomy", "calibration",
}
CAMERA_AXES = ["camera_x_right", "camera_y_down", "camera_z_forward"]


def nonempty(value):
    if type(value) is not str or not value.strip():
        raise ContractError("Depth profile requires nonempty text")
    return value


def validate_manifest(data):
    exact(data, COMMON | {
        "depth_artifact_id", "camera_artifact_id", "camera_format", "device",
        "foreground", "anatomical_frame", "pca_axis_min_separation_ratio",
    })
    if data["schema_version"] != "1.0" or data["source_kind"] != "lidar-depth.v2":
        raise ContractError("Unsupported raw depth source profile")
    for key in ("acquisition_mode", "origin", "processing_mode"):
        nonempty(data[key])
    for key in ("depth_artifact_id", "camera_artifact_id"):
        opaque_id(data[key])
    if (data["acquisition_mode"], data["origin"]) not in {("synthetic", "synthetic_demo"), ("recorded", "automatically_measured")} or data["processing_mode"] != "fresh":
        raise ContractError("Invalid raw-depth acquisition/origin")
    for key in ("subject_ref", "session_ref", "protocol_id", "clock_domain"):
        if data[key] is not None:
            opaque_id(data[key])
    for key in ("pose", "coordinate_frame"):
        if data[key] is not None:
            nonempty(data[key])
    if data["axes"] is not None and data["axes"] != CAMERA_AXES:
        raise ContractError("Raw depth axes must be explicitly optical camera axes or unknown")
    if data["acquired_at"] is not None:
        try:
            stamp = datetime.fromisoformat(nonempty(data["acquired_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError()
        except ValueError:
            raise ContractError("Depth timestamp requires an explicit timezone") from None
    anatomy = exact(data["anatomy"], {"region", "side", "level", "numbering_status"})
    nonempty(anatomy["region"])
    if type(anatomy["side"]) is not str or anatomy["side"] not in {"left", "right", "bilateral", "midline", "unspecified"} or anatomy["level"] is not None or anatomy["numbering_status"] != "not_applicable":
        raise ContractError("Visible raw depth does not assign a vertebral level")
    calibration = exact(data["calibration"], {"status", "source", "author_id", "mm_per_unit"})
    if type(calibration["status"]) is not str or calibration["status"] not in {"verified", "unknown", "invalid"}:
        raise ContractError("Unsupported depth calibration status")
    if calibration["source"] is not None:
        nonempty(calibration["source"])
    if calibration["author_id"] is not None:
        opaque_id(calibration["author_id"])
    if calibration["mm_per_unit"] is not None:
        if finite(calibration["mm_per_unit"], True) > 1000:
            raise ContractError("Depth unit scale exceeds profile")
    if calibration["status"] == "verified" and any(calibration[key] is None for key in ("source", "author_id", "mm_per_unit")):
        raise ContractError("Known metric depth needs source, author and unit scale")
    if type(data["camera_format"]) is not str or data["camera_format"] not in {"texel.scan.xml.v1", "depth-camera-calibration.v1"}:
        raise ContractError("Unsupported raw camera format")
    device = exact(data["device"], {"manufacturer", "model", "serial", "sensor_modality"})
    for key in ("manufacturer", "model", "serial"):
        if device[key] is not None:
            nonempty(device[key])
    if type(device["sensor_modality"]) is not str or device["sensor_modality"] not in {"time_of_flight", "structured_light", "synthetic", "unknown"}:
        raise ContractError("Unsupported depth sensor modality")
    foreground = exact(data["foreground"], {
        "method", "min_depth_units", "max_depth_units", "max_neighbor_delta_units",
        "min_component_pixels", "point_grid_stride",
    })
    if foreground["method"] != "largest_depth_connected_component_v1":
        raise ContractError("Unsupported automatic foreground method")
    low, high, delta = [finite(foreground[key]) for key in ("min_depth_units", "max_depth_units", "max_neighbor_delta_units")]
    if not 0 < low < high <= 65535 or not 0 <= delta <= 65535:
        raise ContractError("Invalid explicit depth connectivity/range")
    if type(foreground["min_component_pixels"]) is not int or not 1 <= foreground["min_component_pixels"] <= 2097152 or type(foreground["point_grid_stride"]) is not int or not 1 <= foreground["point_grid_stride"] <= 16:
        raise ContractError("Unsupported component or point-grid bound")
    ratio = finite(data["pca_axis_min_separation_ratio"])
    if not 1 < ratio <= 100:
        raise ContractError("PCA stability ratio must be explicit and greater than one")
    frame = exact(data["anatomical_frame"], {"status", "up_vector_camera", "source", "author_id"})
    if type(frame["status"]) is not str or frame["status"] not in {"verified", "unknown", "invalid"}:
        raise ContractError("Invalid anatomical orientation status")
    for key in ("source", "author_id"):
        if frame[key] is not None:
            (opaque_id if key == "author_id" else nonempty)(frame[key])
    up = frame["up_vector_camera"]
    if up is not None:
        if type(up) is not list or len(up) != 3 or abs(sum(finite(value) ** 2 for value in up) - 1) > 1e-6:
            raise ContractError("Anatomical up vector must be unit length")
    if frame["status"] == "verified" and (up is None or frame["source"] is None or frame["author_id"] is None or data["axes"] is None or data["coordinate_frame"] is None):
        raise ContractError("Verified anatomical orientation requires evidence and a known optical frame")
    return data


def add_json(context, payload):
    return context.artifacts.add_bytes(json.dumps(payload, allow_nan=False, sort_keys=True, ensure_ascii=False).encode(), "application/json", role="derived")


def color_png(width, height, rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + bytes(row) for row in rows))) + chunk(b"IEND", b"")


def render_depth(context, depth, component, foreground):
    width, height = len(depth[0]), len(depth)
    selected = set(component)
    rows = []
    low, high = foreground["min_depth_units"], foreground["max_depth_units"]
    for v in range(height):
        row = bytearray()
        for u in range(width):
            value = depth[v][u]
            intensity = min(255, max(0, int(255 * (high - value) / (high - low)))) if value else 0
            if v * width + u in selected:
                row.extend((intensity // 3, intensity, min(255, intensity + 25)))
            else:
                row.extend((intensity // 3, intensity // 3, intensity // 3))
        rows.append(row)
    return context.artifacts.add_bytes(color_png(width, height, rows), "image/png", role="derived")


def analyze_depth(request, context):
    request = validate_request(request)
    if (request["module_id"], request["module_version"]) != (RAW_LIDAR_SPEC.module_id, RAW_LIDAR_SPEC.module_version) or request["parameters"] or len(request["input_artifacts"]) != 3:
        raise ContractError("Raw depth requires version 0.2.0, exactly three sources and empty parameters")
    references = {ref["artifact_id"]: ref for ref in request["input_artifacts"]}
    manifests = []
    for reference in request["input_artifacts"]:
        if reference["role"] == "source" and reference["media_type"] == "application/json":
            if reference["size_bytes"] > 1024 * 1024:
                raise ContractError("Depth JSON exceeds profile")
            with context.artifacts.open(reference) as stream:
                payload = loads_json(stream.read(1024 * 1024 + 1))
            if type(payload) is dict and payload.get("source_kind") == "lidar-depth.v2":
                manifests.append((reference, payload))
    if len(manifests) != 1:
        raise ContractError("Exactly one raw-depth acquisition manifest is required")
    source, data = manifests[0]
    validate_manifest(data)
    if request["protocol_id"] != data["protocol_id"]:
        raise ContractError("Depth source and request protocols differ")
    depth_ref, camera_ref = references.get(data["depth_artifact_id"]), references.get(data["camera_artifact_id"])
    if depth_ref is None or camera_ref is None or len({source["artifact_id"], depth_ref["artifact_id"], camera_ref["artifact_id"]}) != 3 or depth_ref["role"] != "source" or camera_ref["role"] != "source" or depth_ref["media_type"] != "image/png":
        raise ContractError("Depth and calibration must be separate declared source artifacts")
    if depth_ref["size_bytes"] > MAX_BYTES:
        raise ContractError("Raw depth artifact exceeds PNG profile before verification")
    if camera_ref["size_bytes"] > 1024 * 1024 or camera_ref["media_type"] not in {"application/json", "application/xml", "text/xml"}:
        raise ContractError("Camera metadata must be bounded XML or JSON")
    with context.artifacts.open(camera_ref) as stream:
        camera = camera_from_bytes(stream.read(1024 * 1024 + 1), data["camera_format"])
    with context.artifacts.open(depth_ref) as stream:
        width, height, rows = decode_depth_png(stream)
    if (width, height) != (camera["width"], camera["height"]):
        raise ContractError("Camera calibration does not match the original depth dimensions")
    foreground = data["foreground"]
    component, qc = largest_component(
        rows, foreground["min_depth_units"], foreground["max_depth_units"],
        foreground["max_neighbor_delta_units"], foreground["min_component_pixels"],
    )
    cal = data["calibration"]
    metric = cal["status"] == "verified"
    scale = cal["mm_per_unit"] if metric else 1
    points, pixels, failed = [], [], 0
    stride = foreground["point_grid_stride"]
    for index in component:
        v, u = divmod(index, width)
        if v % stride or u % stride:
            continue
        point = project_pixel(u, v, rows[v][u], camera, scale)
        if point is None:
            failed += 1
            continue
        points.append(point)
        pixels.append((u, v, rows[v][u]))
        if len(points) > 100000:
            raise ContractError("Cloud exceeds 100000 points; explicitly increase acquisition grid stride")
    geometry = cloud_geometry(points) if cal["status"] != "invalid" else None
    distortion_known = camera["distortion"]["model"] != "unknown"
    qc.update({
        "zero_depth_pixels": sum(value == 0 for row in rows for value in row),
        "nonzero_depth_pixels": sum(value != 0 for row in rows for value in row),
        "point_count": len(points), "projection_failed_pixels": failed,
        "component_touches_image_border": any(index // width in {0, height - 1} or index % width in {0, width - 1} for index in component),
        "geometry_distortion_verified": distortion_known,
        "calibration_status": cal["status"], "physical_accuracy_mm": None,
        "foreground_identity": "largest_depth_continuous_component; subject identity not anatomically detected",
        "visible_surface_only": True, "view_count": 1,
    })
    quality = "unreliable" if geometry is None or cal["status"] == "invalid" or not distortion_known or failed or qc["component_touches_image_border"] else "usable" if metric else "unknown"
    comparison_calibration = "invalid" if geometry is None or cal["status"] == "invalid" or failed else "verified" if metric and distortion_known else "unknown"
    qc["comparison_geometry_calibration_status"] = comparison_calibration
    frame = data["anatomical_frame"]
    tilt = None
    tilt_reason = "anatomical_orientation_invalid" if frame["status"] == "invalid" else "anatomical_orientation_unknown"
    if geometry is not None and frame["status"] == "verified":
        principal = geometry["principal_axes"]
        if principal[0]["eigenvalue"] > 0 and principal[0]["eigenvalue"] >= data["pca_axis_min_separation_ratio"] * principal[1]["eigenvalue"]:
            cosine = abs(sum(a * b for a, b in zip(principal[0]["axis_camera"], frame["up_vector_camera"])))
            tilt = math.degrees(math.acos(min(1, max(0, cosine))))
            tilt_reason = None
        else:
            tilt_reason = "principal_axis_not_separated"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "cloud.ply"
        with path.open("w", encoding="ascii") as stream:
            stream.write("ply\nformat ascii 1.0\ncomment Automatically projected original depth; visible connected component only\ncomment unit " + ("mm" if metric else "source_depth_units") + "\nelement vertex " + str(len(points)) + "\nproperty double x\nproperty double y\nproperty double z\nproperty uint source_u\nproperty uint source_v\nproperty ushort raw_depth\nend_header\n")
            for point, pixel in zip(points, pixels):
                stream.write(" ".join(format(value, ".15g") for value in point) + " " + " ".join(str(value) for value in pixel) + "\n")
        cloud_ref = context.artifacts.add_file(path, "application/x-ply", role="derived")
        path = Path(directory) / "component.csv"
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["source_u", "source_v", "raw_depth_units"])
            for index in component:
                v, u = divmod(index, width)
                writer.writerow([u, v, rows[v][u]])
        component_ref = context.artifacts.add_file(path, "text/csv", role="derived")
    preview_ref = render_depth(context, rows, component, foreground)
    method = {
        "schema_version": "lidar-depth-method.v2", "implementation": "stdlib-png16-component-pinhole-brown-jacobi.v1",
        "foreground": foreground, "camera": camera,
        "projection": "axial depth Z; X=undistorted_ray_x*Z, Y=undistorted_ray_y*Z; no estimated body-height scaling",
        "distortion_handling": "Brown-Conrady inverse Newton with forward residual below 1e-10, or documented rectified pinhole; unknown post-rectification camera geometry remains unreliable",
        "sensor_axes": CAMERA_AXES, "unit": "mm" if metric else "source_depth_units",
        "pca": "population covariance; deterministic symmetric 3x3 Jacobi eigenvectors, descending eigenvalue; extent=max(projection)-min(projection)",
        "axis_tilt": "acos(abs(dot(first PCA axis, explicitly verified anatomical up))); not torso/skeletal/Cobb angle",
        "pca_axis_min_separation_ratio": data["pca_axis_min_separation_ratio"],
        "anatomical_frame": frame,
        "coverage": "one visible connected surface; no hole filling, surface fitting or body model",
        "clinical_interpretation": "external geometric measurements for review only; no clinical thresholds",
    }
    method_ref = add_json(context, method)
    measurements = []
    for axis in range(3):
        extent = geometry["sensor_bounds"][axis][1] - geometry["sensor_bounds"][axis][0] if geometry is not None and metric else None
        measurements.append({"id": "sensor-" + str(axis), "concept": "surface.visible_extent", "value": extent, "unit": "mm", "axis_camera": CAMERA_AXES[axis], "quality": quality if extent is not None else "unreliable" if quality == "unreliable" else "unknown"})
    for axis in range(3):
        extent = geometry["principal_axes"][axis]["extent"] if geometry is not None and metric else None
        measurements.append({"id": "principal-" + str(axis), "concept": "surface.principal_extent", "value": extent, "unit": "mm", "axis_camera": geometry["principal_axes"][axis]["axis_camera"] if geometry is not None else None, "quality": quality if extent is not None else "unreliable" if quality == "unreliable" else "unknown"})
    measurements.append({"id": "principal-axis-up", "concept": "posture.surface_axis_tilt", "value": tilt, "unit": "deg", "axis_camera": geometry["principal_axes"][0]["axis_camera"] if geometry is not None else None, "quality": quality if tilt is not None else "unreliable" if geometry is None or frame["status"] == "invalid" else "unknown", "not_assessed_reason": tilt_reason})
    summary = {
        "schema_version": "lidar-depth-measurements.v2", "source_kind": "lidar.depth.measurements.v2",
        "manifest_artifact_id": source["artifact_id"], "depth_artifact_id": depth_ref["artifact_id"],
        "camera_artifact_id": camera_ref["artifact_id"], "cloud_artifact_id": cloud_ref["artifact_id"],
        "component_artifact_id": component_ref["artifact_id"], "method_artifact_id": method_ref["artifact_id"],
        "image_shape_yx": [height, width], "camera": camera, "calibration": cal,
        "anatomical_frame": frame,
        "geometry": geometry, "geometry_unit": "mm" if metric else "source_depth_units",
        "qc": qc, "measurements": measurements,
        "automatic_anatomical_landmarks": None, "anatomical_asymmetry": None,
        "spinal_curvature": None, "range_of_motion": None, "repeatability_mm": None,
        "comparison_metadata": {
            "schema_version": "1.0", "subject_ref": data["subject_ref"], "session_ref": data["session_ref"],
            "protocol_id": data["protocol_id"], "linkage_evidence_refs": [],
            "acquired_interval": {"start": data["acquired_at"], "end": data["acquired_at"], "clock_domain": data["clock_domain"]},
            "event_id": None, "pose_id": data["pose"], "calibration_status": comparison_calibration,
            "calibration_evidence_refs": [{"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/calibration", "scope": "depth_unit_scale_only"}}, {"artifact_id": camera_ref["artifact_id"], "selector": {"kind": "camera_intrinsics", "scope": "nominal_projection_intrinsics"}}] if metric else [],
            "frame": {"id": data["coordinate_frame"], "units": "mm" if metric else None, "axes": data["axes"]}, "transform_refs": [],
        },
    }
    summary_ref = add_json(context, summary)
    observations = []
    for measurement in measurements:
        evidence = [
            {"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/anatomical_frame" if measurement["concept"] == "posture.surface_axis_tilt" else "/calibration"}},
            {"artifact_id": depth_ref["artifact_id"], "selector": {"kind": "image", "frame_index": 0, "coordinate_frame": "original-depth-pixels"}},
            {"artifact_id": camera_ref["artifact_id"], "selector": {"kind": "camera_intrinsics", "format": data["camera_format"]}},
            {"artifact_id": component_ref["artifact_id"], "selector": {"kind": "automatic_component", "component_id": "largest"}},
            {"artifact_id": cloud_ref["artifact_id"], "selector": {"kind": "projected_cloud", "axis": measurement["axis_camera"], "source_pixel_properties": ["source_u", "source_v", "raw_depth"]}},
            {"artifact_id": method_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/"}},
            {"artifact_id": summary_ref["artifact_id"], "selector": {"kind": "measurement", "measurement_id": measurement["id"]}},
            {"artifact_id": preview_ref["artifact_id"], "selector": {"kind": "image", "frame_index": 0}},
        ]
        observations.append({
            "observation_id": observation_id_for(request["job_id"], measurement["concept"], measurement["id"]),
            "concept": measurement["concept"], "assessment_status": "assessed" if measurement["value"] is not None else "not_assessed",
            "value": measurement["value"], "unit": measurement["unit"], "anatomy": data["anatomy"],
            "quality": measurement["quality"], "origin": data["origin"],
            "method": "automatic_depth_component_" + ("sensor_extent_v2" if measurement["concept"] == "surface.visible_extent" else "jacobi_pca_extent_v2" if measurement["concept"] == "surface.principal_extent" else "verified_up_pca_tilt_v2"),
            "evidence_refs": evidence, "model": None, "acquired_at": data["acquired_at"],
            "coordinate_frame": data["coordinate_frame"],
        })
    partial = any(obs["value"] is None or obs["quality"] != "usable" for obs in observations)
    warnings = [{"code": "visible_surface_descriptive_only", "message": "Automatic geometry describes one visible depth component, not skeletal landmarks, Cobb angle, spinal curvature, pain cause or a diagnosis."}]
    if not distortion_known:
        warnings.append({"code": "depth_distortion_geometry_unverified", "message": "Post-rectification camera geometry is not verified; cloud and nominal metric extents are unreliable."})
    if frame["status"] != "verified":
        warnings.append({"code": "anatomical_orientation_unknown", "message": "Sensor axes do not establish anatomical up or sides; anatomical tilt/asymmetry is not assessed."})
    if geometry is None:
        warnings.append({"code": "depth_component_unusable", "message": "No sufficiently large usable automatic depth component was available."})
    return validate_result(result_envelope(request, observations, [source, depth_ref, camera_ref, cloud_ref, component_ref, preview_ref, method_ref, summary_ref], "partial" if partial else "complete", warnings), request)
