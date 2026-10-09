"""ASCII surface import and numerical measurements of authored external landmarks."""
import html
import json
import math
from pathlib import Path
import tempfile

from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope
from ._support import COMMON_FIELDS, add_json, author, calibration, common, line, metadata, number, observation, png, read_source, require

_MAX_VERTICES = 100000


def _lines(stream):
    while True:
        raw = stream.readline(65537)
        if not raw:
            return
        if len(raw) > 65536:
            raise ContractError("Geometry line exceeds supported size")
        try:
            yield raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise ContractError("Geometry requires the supported ASCII profile") from None


def _point(tokens):
    try:
        values = [number(float(item)) for item in tokens]
    except ValueError:
        raise ContractError("Geometry coordinate is invalid") from None
    if len(values) != 3 or any(abs(value) > 1e9 for value in values):
        raise ContractError("Geometry coordinate exceeds supported bounds")
    return values


def import_geometry(stream, format_name):
    rows = iter(_lines(stream))
    vertices, faces = [], []
    try:
        if format_name == "obj":
            for row in rows:
                tokens = row.split()
                if not tokens or tokens[0].startswith("#"):
                    continue
                if tokens[0] == "v":
                    vertices.append(_point(tokens[1:]))
                elif tokens[0] == "f":
                    indices = [int(item.split("/")[0]) for item in tokens[1:]]
                    indices = [index - 1 if index > 0 else len(vertices) + index for index in indices]
                    faces.append(indices)
                elif tokens[0] not in {"vt", "vn", "o", "g", "s", "usemtl", "mtllib"}:
                    raise ContractError("Unsupported OBJ record")
                if len(vertices) > _MAX_VERTICES or len(faces) > _MAX_VERTICES:
                    raise ContractError("Geometry exceeds supported element count")
        elif format_name == "ply":
            if next(rows) != "ply" or next(rows) != "format ascii 1.0":
                raise ContractError("Only ASCII PLY 1.0 is supported")
            element, vertex_count, face_count = None, None, 0
            vertex_props, face_props, order = [], [], []
            for count, row in enumerate(rows):
                if count > 512:
                    raise ContractError("PLY header exceeds supported size")
                tokens = row.split()
                if tokens[0] == "end_header":
                    break
                if tokens[0] in {"comment", "obj_info"}:
                    continue
                if tokens[0] == "element" and len(tokens) == 3:
                    element = tokens[1]
                    if element not in {"vertex", "face"} or element in order:
                        raise ContractError("Unsupported PLY elements")
                    order.append(element)
                    size = int(tokens[2])
                    if not 0 <= size <= _MAX_VERTICES:
                        raise ContractError("PLY element count exceeds supported bounds")
                    if element == "vertex": vertex_count = size
                    else: face_count = size
                elif tokens[0] == "property" and element == "vertex" and len(tokens) == 3:
                    vertex_props.append(tokens[2])
                elif tokens[0] == "property" and element == "face":
                    face_props.append(tokens)
                else:
                    raise ContractError("Unsupported PLY property")
            else:
                raise ContractError("PLY header is incomplete")
            if order not in (["vertex"], ["vertex", "face"]) or not vertex_count or len(vertex_props) != len(set(vertex_props)) or not {"x", "y", "z"} <= set(vertex_props):
                raise ContractError("PLY vertex layout is unsupported")
            if face_count and (len(face_props) != 1 or len(face_props[0]) != 5 or face_props[0][1] != "list" or face_props[0][-1] not in {"vertex_indices", "vertex_index"}):
                raise ContractError("PLY face layout is unsupported")
            for _ in range(vertex_count):
                tokens = next(rows).split()
                if len(tokens) != len(vertex_props):
                    raise ContractError("PLY vertex row is inconsistent")
                vertices.append(_point([tokens[vertex_props.index(axis)] for axis in ("x", "y", "z")]))
            for _ in range(face_count):
                tokens = [int(item) for item in next(rows).split()]
                if not tokens or tokens[0] != len(tokens) - 1:
                    raise ContractError("PLY face row is inconsistent")
                faces.append(tokens[1:])
            if any(row for row in rows):
                raise ContractError("PLY contains unexpected trailing records")
        else:
            raise ContractError("Unsupported surface format")
    except (StopIteration, ValueError, IndexError):
        raise ContractError("Surface geometry is incomplete or malformed") from None
    if not vertices:
        raise ContractError("Surface geometry has no vertices")
    for face in faces:
        if not 3 <= len(face) <= 32 or len(face) != len(set(face)) or any(not 0 <= index < len(vertices) for index in face):
            raise ContractError("Surface face indices are invalid")
    return vertices, faces


class LidarPostureProvider:
    spec = ProviderSpec("lidar.posture", "0.1.0", "External surface and manual landmark measurements")

    def analyze(self, request, context):
        request, data, source = read_source(request, context, self.spec)
        require(data, COMMON_FIELDS | {"geometry_artifact_id", "format", "coverage", "annotations"})
        common(data, "lidar-manual.v1", 3)
        cal = calibration(data, {"mm_per_unit"})
        scale = None if cal["mm_per_unit"] is None else number(cal["mm_per_unit"], positive=True)
        known_scale = cal["status"] == "verified" and scale is not None
        if cal["status"] == "verified" and scale is None:
            raise ContractError("Verified surface scale requires mm per source unit")
        if data["coverage"] not in {"complete", "partial", "unknown"}:
            raise ContractError("Unsupported surface coverage")
        references = {ref["artifact_id"]: ref for ref in request["input_artifacts"]}
        geometry = references.get(data["geometry_artifact_id"])
        if len(references) != 2 or geometry is None or geometry["role"] != "source" or geometry == source:
            raise ContractError("Surface profile requires its declared geometry artifact")
        with context.artifacts.open(geometry) as stream:
            vertices, faces = import_geometry(stream, data["format"])
        annotations = require(data["annotations"], {"author", "landmarks", "measurements"})
        author(annotations["author"])
        if type(annotations["landmarks"]) is not dict or type(annotations["measurements"]) is not list or not annotations["measurements"]:
            raise ContractError("Surface profile requires authored landmarks and measurements")
        for key, index in annotations["landmarks"].items():
            opaque_id(key)
            if type(index) is not int or not 0 <= index < len(vertices):
                raise ContractError("Landmark vertex reference is invalid")
        measures, ids = [], set()
        for item in annotations["measurements"]:
            require(item, {"id", "kind", "landmarks"})
            opaque_id(item["id"])
            if item["id"] in ids or item["kind"] not in {"distance", "angle"}:
                raise ContractError("Surface measurement identity or definition is invalid")
            ids.add(item["id"])
            names = item["landmarks"]
            if type(names) is not list or len(names) != (2 if item["kind"] == "distance" else 3) or any(type(name) is not str for name in names):
                raise ContractError("Surface measurement requires declared landmark names")
            points = [vertices[annotations["landmarks"][name]] for name in names] if all(name in annotations["landmarks"] for name in names) else None
            value, reason = None, None
            if points is None:
                reason = "missing_landmark"
            elif item["kind"] == "distance":
                if known_scale:
                    value = math.dist(*points) * scale
                else:
                    reason = "unknown_or_invalid_scale"
            else:
                first = [a - b for a, b in zip(points[0], points[1])]
                second = [a - b for a, b in zip(points[2], points[1])]
                first_norm, second_norm = math.sqrt(sum(v*v for v in first)), math.sqrt(sum(v*v for v in second))
                if first_norm == 0 or second_norm == 0:
                    reason = "degenerate_angle"
                else:
                    cosine = sum(a*b for a, b in zip(first, second)) / (first_norm * second_norm)
                    value = math.degrees(math.acos(max(-1, min(1, cosine))))
            measures.append({**item, "points_source_units": points, "value": value,
                             "unit": "mm" if item["kind"] == "distance" else "deg", "reason": reason,
                             "uncertainty": None, "repeatability": None})
        partial = any(item["value"] is None for item in measures) or data["coverage"] != "complete"
        warnings = [{"code": "surface_measurement_incomplete", "message": "Some surface measurements or coverage are unknown; this is external anatomy only."}] if partial else []
        annotation_ref = add_json(context, {"schema_version": "1.0", "source_kind": "lidar.annotations.v1", "geometry_artifact_id": geometry["artifact_id"], **annotations})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.json"
            output_vertices = [[value * scale for value in point] for point in vertices] if known_scale else vertices
            with path.open("w") as stream:
                json.dump({"schema_version": "1.0", "kind": "surface_mesh", "vertices": output_vertices, "faces": faces,
                           "units": "mm" if known_scale else "source_units", "source_mm_per_unit": scale if known_scale else None,
                           "coordinate_frame": data["coordinate_frame"], "source_artifact_id": geometry["artifact_id"]}, stream, allow_nan=False)
            geometry_ref = context.artifacts.add_file(path, "application/json", role="derived")
            ply_path = Path(directory) / "surface.ply"
            with ply_path.open("w") as stream:
                stream.write("ply\nformat ascii 1.0\ncomment units " + ("mm" if known_scale else "unknown_source_units") + "\n")
                stream.write(f"element vertex {len(vertices)}\nproperty double x\nproperty double y\nproperty double z\nelement face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n")
                for point in output_vertices: stream.write(" ".join(format(value, ".17g") for value in point) + "\n")
                for face in faces: stream.write(str(len(face)) + " " + " ".join(str(index) for index in face) + "\n")
            viewable_geometry_ref = context.artifacts.add_file(ply_path, "application/x-ply", role="derived")
        preview_refs = self._preview(context, vertices, annotations, measures)
        summary = {"schema_version": "1.0", "source_kind": "lidar.measurements.v1", "source_artifact_id": source["artifact_id"],
                   "geometry_artifact_id": geometry["artifact_id"], "annotations_artifact_id": annotation_ref["artifact_id"],
                   "viewable_geometry_artifact_id": viewable_geometry_ref["artifact_id"],
                   "acquired_at": data["acquired_at"], "pose": data["pose"], "calibration": cal,
                   "measurements": measures, "comparison_metadata": metadata(data, source, "mm" if known_scale else "source_units", "invalid" if cal["status"] == "invalid" else "verified" if known_scale else "unknown")}
        measurement_ref = add_json(context, summary)
        artifacts = [source, geometry, annotation_ref, geometry_ref, viewable_geometry_ref, *preview_refs, measurement_ref]
        observations = []
        for item in measures:
            evidence = [{"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/annotations"}},
                        {"artifact_id": geometry["artifact_id"], "selector": {"kind": "geometry", "vertex_indices": [annotations["landmarks"].get(name) for name in item["landmarks"]]}},
                        {"artifact_id": measurement_ref["artifact_id"], "selector": {"kind": "measurement", "measurement_id": item["id"], "frame_index": 0}},
                        {"artifact_id": preview_refs[1]["artifact_id"], "selector": {"kind": "image", "frame_index": 0, "coordinate_frame": data["coordinate_frame"]}}]
            quality = "unreliable" if item["value"] is None and cal["status"] == "invalid" else "unknown" if item["value"] is None else "usable" if data["coverage"] == "complete" else "unreliable"
            observations.append(observation(request, data, "posture.landmark_" + item["kind"], item["id"], item["value"], item["unit"], quality,
                                            "manual_surface_landmarks_" + ("euclidean_distance_v1" if item["kind"] == "distance" else "three_point_angle_v1"), evidence))
        return validate_result(result_envelope(request, observations, artifacts, "partial" if partial else "complete", warnings), request)

    @staticmethod
    def _preview(context, vertices, annotations, measures):
        minima = [min(point[axis] for point in vertices) for axis in (0, 1)]
        spans = [max(point[axis] for point in vertices) - minima[axis] for axis in (0, 1)]
        factor = 216 / max(*spans, 1e-12)
        def project(point): return [20 + (point[0] - minima[0])*factor, 236 - (point[1] - minima[1])*factor]
        rows = [bytearray([15, 20, 30]*256) for _ in range(256)]
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="256" height="280" viewBox="0 0 256 280"><rect width="256" height="280" fill="#0f141e"/>']
        for point in vertices[::max(1, len(vertices)//10000)]:
            projected = project(point)
            line(rows, projected, projected, (100, 110, 125))
        for item in measures:
            if item["points_source_units"]:
                projected = [project(point) for point in item["points_source_units"]]
                for a, b in zip(projected, projected[1:]): line(rows, a, b)
                svg.append('<polyline points="' + ' '.join(f'{p[0]:.3f},{p[1]:.3f}' for p in projected) + '" fill="none" stroke="#00ffff"/>')
        for key, index in annotations["landmarks"].items():
            point = project(vertices[index])
            svg.append(f'<text x="{point[0]:.3f}" y="{point[1]:.3f}" fill="white">{html.escape(key)}</text>')
        svg.append('<text x="8" y="272" fill="white" font-size="10">External surface: XY projection, anatomy landmarks</text></svg>')
        return [context.artifacts.add_bytes(''.join(svg).encode(), "image/svg+xml", role="derived"),
                context.artifacts.add_bytes(png(256, 256, rows), "image/png", role="derived")]
