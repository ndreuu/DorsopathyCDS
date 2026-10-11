"""Depth projection, deterministic connected component and 3x3 Jacobi PCA."""
import math
import xml.etree.ElementTree as ET

from dorsopathy_platform.contracts import ContractError, loads_json


def finite(value, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value) or positive and value <= 0:
        raise ContractError("Depth metadata requires finite supported numbers")
    return float(value)


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ContractError("Depth metadata requires exactly documented fields")
    return value


def camera_from_bytes(raw, format_name):
    if len(raw) > 1024 * 1024:
        raise ContractError("Camera metadata exceeds limit")
    if format_name == "texel.scan.xml.v1":
        try:
            # The published XML declares UTF-16 but its actual bytes are UTF-8.
            # Decode to Unicode before parsing; retain the untouched source.
            document = raw.decode("utf-8-sig")
            if "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
                raise ContractError("XML entities and DTD are unsupported")
            root = ET.fromstring(document)
            depth_nodes = root.findall(".//stage/stream/depth")
            if len(depth_nodes) != 1:
                raise ContractError("TEXEL profile requires one depth camera")
            depth = depth_nodes[0]
            intrinsics = depth.find("intrinsics")
            if intrinsics is None:
                raise ContractError("Camera intrinsics are missing")
            result = {
                "schema_version": "depth-camera-calibration.v1",
                "width": int(depth.attrib["width"]), "height": int(depth.attrib["height"]),
                "intrinsics": {key: float(intrinsics.attrib[key]) for key in ("cx", "cy", "fx", "fy")},
                "distortion": {"model": "unknown", "coefficients": None, "source": "TEXEL reports OpenCV distortion mitigation, but post-rectification camera matrix/ray residual is unverified"},
                "depth_convention": "axial_z",
            }
        except (UnicodeDecodeError, ET.ParseError, KeyError, ValueError):
            raise ContractError("TEXEL depth-camera metadata is malformed") from None
    elif format_name == "depth-camera-calibration.v1":
        result = loads_json(raw)
    else:
        raise ContractError("Unsupported camera calibration profile")
    exact(result, {"schema_version", "width", "height", "intrinsics", "distortion", "depth_convention"})
    if result["schema_version"] != "depth-camera-calibration.v1" or result["depth_convention"] != "axial_z":
        raise ContractError("Only explicit axial-Z depth is supported")
    for key in ("width", "height"):
        if type(result[key]) is not int or not 1 <= result[key] <= 4096:
            raise ContractError("Invalid camera dimensions")
    intr = exact(result["intrinsics"], {"cx", "cy", "fx", "fy"})
    finite(intr["fx"], True)
    finite(intr["fy"], True)
    for key, size in (("cx", result["width"]), ("cy", result["height"])):
        if not -size <= finite(intr[key]) <= 2 * size:
            raise ContractError("Camera principal point is out of supported bounds")
    distortion = exact(result["distortion"], {"model", "coefficients", "source"})
    if type(distortion["model"]) is not str or distortion["model"] not in {"none", "brown_conrady", "unknown"}:
        raise ContractError("Unsupported depth distortion model")
    if distortion["source"] is not None and (type(distortion["source"]) is not str or not distortion["source"].strip()):
        raise ContractError("Invalid distortion provenance")
    if distortion["model"] != "unknown" and distortion["source"] is None:
        raise ContractError("Known rectification/distortion requires provenance")
    if distortion["model"] == "brown_conrady":
        coefficients = distortion["coefficients"]
        if type(coefficients) is not list or len(coefficients) != 5 or any(abs(finite(value)) > 5 for value in coefficients):
            raise ContractError("Brown-Conrady requires bounded k1,k2,p1,p2,k3")
    elif distortion["coefficients"] is not None:
        raise ContractError("Non-Brown distortion must not invent coefficients")
    return result


def inverse_brown(xd, yd, coefficients):
    k1, k2, p1, p2, k3 = coefficients
    x, y = xd, yd
    for _ in range(30):
        r2 = x * x + y * y
        radial = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
        fx = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x) - xd
        fy = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y - yd
        if max(abs(fx), abs(fy)) < 1e-10:
            return x, y
        derivative = k1 + 2 * k2 * r2 + 3 * k3 * r2 * r2
        dx, dy = 2 * x * derivative, 2 * y * derivative
        a = radial + x * dx + 2 * p1 * y + 6 * p2 * x
        b = x * dy + 2 * p1 * x + 2 * p2 * y
        c = y * dx + 2 * p1 * x + 2 * p2 * y
        d = radial + y * dy + 6 * p1 * y + 2 * p2 * x
        determinant = a * d - b * c
        if determinant <= 1e-12 or not math.isfinite(determinant):
            return None
        x -= (fx * d - fy * b) / determinant
        y -= (fy * a - fx * c) / determinant
        if not math.isfinite(x + y) or abs(x) + abs(y) > 10:
            return None
    return None


def project_pixel(u, v, depth, camera, scale):
    intr = camera["intrinsics"]
    x, y = (u - intr["cx"]) / intr["fx"], (v - intr["cy"]) / intr["fy"]
    if camera["distortion"]["model"] == "brown_conrady":
        ray = inverse_brown(x, y, camera["distortion"]["coefficients"])
        if ray is None:
            return None
        x, y = ray
    if abs(x) + abs(y) > 10:
        return None
    z = depth * scale
    return [x * z, y * z, z]


def largest_component(rows, low, high, neighbor_delta, minimum):
    height, width = len(rows), len(rows[0])
    visited = bytearray(width * height)
    best, candidates, sizes = [], 0, []
    for v in range(height):
        for u in range(width):
            if low <= rows[v][u] <= high and rows[v][u] != 0:
                candidates += 1
    for index in range(width * height):
        v, u = divmod(index, width)
        if visited[index] or not low <= rows[v][u] <= high or rows[v][u] == 0:
            continue
        stack, component = [index], []
        visited[index] = 1
        while stack:
            current = stack.pop()
            cy, cx = divmod(current, width)
            component.append(current)
            neighbors = []
            if cx:
                neighbors.append(current - 1)
            if cx + 1 < width:
                neighbors.append(current + 1)
            if cy:
                neighbors.append(current - width)
            if cy + 1 < height:
                neighbors.append(current + width)
            for adjacent in neighbors:
                if visited[adjacent]:
                    continue
                ay, ax = divmod(adjacent, width)
                depth = rows[ay][ax]
                if depth and low <= depth <= high and abs(depth - rows[cy][cx]) <= neighbor_delta:
                    visited[adjacent] = 1
                    stack.append(adjacent)
        sizes.append(len(component))
        if len(component) > len(best):
            best = component
    if len(best) < minimum:
        best = []
    best.sort()
    return best, {"candidate_pixels": candidates, "component_count": len(sizes), "largest_component_pixels": len(best), "largest_component_fraction": len(best) / candidates if candidates else None, "other_component_sizes_largest_first": sorted(sizes, reverse=True)[:20]}


def symmetric_eigen(matrix):
    a = [row[:] for row in matrix]
    vectors = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(50):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pair: abs(a[pair[0]][pair[1]]))
        if abs(a[p][q]) <= 1e-12 * max(1, max(abs(a[i][i]) for i in range(3))):
            break
        angle = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(angle), math.sin(angle)
        rotation = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
        rotation[p][p], rotation[p][q], rotation[q][p], rotation[q][q] = c, s, -s, c
        temp = [[sum(a[i][k] * rotation[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        a = [[sum(rotation[k][i] * temp[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        vectors = [[sum(vectors[i][k] * rotation[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    pairs = []
    for index in range(3):
        vector = [vectors[row][index] for row in range(3)]
        sign = 1 if vector[max(range(3), key=lambda i: abs(vector[i]))] >= 0 else -1
        pairs.append((max(0, a[index][index]), [sign * value for value in vector]))
    return sorted(pairs, key=lambda pair: pair[0], reverse=True)


def cloud_geometry(points):
    if len(points) < 3:
        return None
    center = [math.fsum(point[axis] for point in points) / len(points) for axis in range(3)]
    covariance = [[math.fsum((point[i] - center[i]) * (point[j] - center[j]) for point in points) / len(points) for j in range(3)] for i in range(3)]
    principal = symmetric_eigen(covariance)
    extents = []
    for eigenvalue, axis in principal:
        projections = [sum((point[i] - center[i]) * axis[i] for i in range(3)) for point in points]
        extents.append({"eigenvalue": eigenvalue, "axis_camera": axis, "extent": max(projections) - min(projections)})
    return {
        "centroid": center, "covariance": covariance,
        "sensor_bounds": [[min(point[i] for point in points), max(point[i] for point in points)] for i in range(3)],
        "principal_axes": extents,
    }
