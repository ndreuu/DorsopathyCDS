"""ROI arithmetic on an explicitly decoded Celsius export, not a camera decoder."""
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope
from ._support import COMMON_FIELDS, add_json, calibration, common, metadata, number, observation, png, read_source, require


class ThermographyProvider:
    spec = ProviderSpec("thermography", "0.1.0", "Decoded temperature matrix and explicit ROIs")

    def analyze(self, request, context):
        request, data, source = read_source(request, context, self.spec)
        require(data, COMMON_FIELDS | {"unit", "temperature_matrix", "conditions", "rois", "pairs"})
        common(data, "thermography-matrix.v1", 2)
        cal = calibration(data)
        if len(request["input_artifacts"]) != 1 or data["unit"] != "Cel":
            raise ContractError("Thermography requires one decoded Celsius matrix JSON")
        matrix = data["temperature_matrix"]
        if type(matrix) is not list or not matrix or len(matrix) > 1024 or type(matrix[0]) is not list or not matrix[0] or len(matrix[0]) > 1024:
            raise ContractError("Temperature matrix dimensions are unsupported")
        width, height = len(matrix[0]), len(matrix)
        for row in matrix:
            if type(row) is not list or len(row) != width:
                raise ContractError("Temperature matrix must be rectangular")
            for value in row:
                if value is not None and number(value) < -273.15:
                    raise ContractError("Celsius export contains an invalid temperature")
        conditions = require(data["conditions"], {"emissivity", "ambient_Cel", "distance_m"})
        if conditions["emissivity"] is not None and not 0 < number(conditions["emissivity"]) <= 1:
            raise ContractError("Emissivity must be a known fraction or null")
        if conditions["ambient_Cel"] is not None: number(conditions["ambient_Cel"])
        if conditions["distance_m"] is not None: number(conditions["distance_m"], positive=True)
        conditions_known = all(value is not None for value in conditions.values())
        if type(data["rois"]) is not list or not data["rois"] or type(data["pairs"]) is not list:
            raise ContractError("Temperature profile requires ROI and pair lists")
        rois, measurements, ids = {}, [], set()
        for item in data["rois"]:
            require(item, {"id", "side", "pixels"}); opaque_id(item["id"])
            if item["id"] in rois or item["side"] not in {"left", "right", "midline", "bilateral", "unspecified"}:
                raise ContractError("Temperature ROI identity or side is invalid")
            pixels = item["pixels"]
            if type(pixels) is not list or not pixels:
                raise ContractError("Temperature ROI must contain pixels")
            seen, values = set(), []
            invalid = False
            for point in pixels:
                if type(point) is not list or len(point) != 2 or any(type(coordinate) is not int for coordinate in point):
                    raise ContractError("Temperature ROI uses integer x/y pixels")
                x, y = point
                if (x, y) in seen: raise ContractError("Temperature ROI pixels must be unique")
                seen.add((x, y))
                if not (0 <= x < width and 0 <= y < height) or matrix[y][x] is None:
                    invalid = True
                else:
                    values.append(float(matrix[y][x]))
            reason = "invalid_or_missing_roi_pixels" if invalid else "unknown_or_invalid_calibration" if cal["status"] != "verified" else None
            mean = sum(values) / len(values) if values and reason is None else None
            record = {"id": item["id"], "kind": "roi", "side": item["side"], "pixels": pixels,
                      "mean_Cel": mean, "min_Cel": min(values) if mean is not None else None,
                      "max_Cel": max(values) if mean is not None else None, "valid_pixel_count": len(values),
                      "requested_pixel_count": len(pixels), "reason": reason}
            rois[item["id"]] = record
            measurements.append(record)
            ids.add(item["id"])
        for item in data["pairs"]:
            require(item, {"id", "left", "right", "compatibility"}); opaque_id(item["id"])
            opaque_id(item["left"]); opaque_id(item["right"])
            if item["id"] in ids or item["compatibility"] not in {"compatible", "incompatible", "unknown"}:
                raise ContractError("Temperature pair identity or compatibility is invalid")
            ids.add(item["id"])
            left, right = rois.get(item["left"]), rois.get(item["right"])
            valid = left is not None and right is not None and left["side"] == "left" and right["side"] == "right" and left["mean_Cel"] is not None and right["mean_Cel"] is not None and item["compatibility"] == "compatible" and conditions_known
            measurements.append({**item, "kind": "pair", "delta_K": left["mean_Cel"] - right["mean_Cel"] if valid else None,
                                 "formula": "mean(left)-mean(right)", "reason": None if valid else "pair_or_conditions_not_compatible"})
        partial = not conditions_known or any(item.get("mean_Cel") is None if item["kind"] == "roi" else item["delta_K"] is None for item in measurements)
        previews = self._heatmap(context, matrix, rois, data["coordinate_frame"])
        summary = {"schema_version": "1.0", "source_kind": "thermography.measurements.v1", "source_artifact_id": source["artifact_id"],
                   "unit": "Cel", "matrix_axes": "row_y,column_x", "pose": data["pose"], "acquired_at": data["acquired_at"],
                   "conditions": conditions, "calibration": cal, "measurements": measurements,
                   "comparison_metadata": metadata(data, source, "px", cal["status"])}
        measurement_ref = add_json(context, summary)
        observations = []
        for item in measurements:
            pair = item["kind"] == "pair"
            value = item["delta_K"] if pair else item["mean_Cel"]
            evidence = [{"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/temperature_matrix"}},
                        {"artifact_id": measurement_ref["artifact_id"], "selector": {"kind": "measurement", "measurement_id": item["id"], "frame_index": 0}},
                        {"artifact_id": previews[1]["artifact_id"], "selector": {"kind": "image", "frame_index": 0, "coordinate_frame": data["coordinate_frame"]}}]
            quality = "unreliable" if cal["status"] == "invalid" else "usable" if value is not None and conditions_known else "unknown"
            observations.append(observation(request, data, "thermal.left_right_delta" if pair else "thermal.roi_mean_temperature",
                                            item["id"], value, "K" if pair else "Cel", quality,
                                            "decoded_temperature_matrix_left_minus_right_v1" if pair else "decoded_temperature_matrix_manual_roi_mean_v1",
                                            evidence, "bilateral" if pair else item["side"]))
        warnings = [{"code": "temperature_measurement_incomplete", "message": "Calibration, ROI pixels or acquisition conditions are unknown; temperature is not a diagnosis."}] if partial else []
        return validate_result(result_envelope(request, observations, [source, *previews, measurement_ref], "partial" if partial else "complete", warnings), request)

    @staticmethod
    def _heatmap(context, matrix, rois, coordinate_frame):
        valid = [value for row in matrix for value in row if value is not None]
        low, high = (min(valid), max(valid)) if valid else (0, 1)
        span = max(high - low, 1e-12)
        height, width = len(matrix), len(matrix[0])
        rows = []
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="560" viewBox="0 0 {width+1} {height+0.25}">']
        outlined = {tuple(point) for roi in rois.values() for point in roi["pixels"]}
        for y, row in enumerate(matrix):
            rgb = bytearray()
            for x, value in enumerate(row):
                fraction = (value - low)/span if value is not None else None
                color = (int(fraction*255), 50, int((1-fraction)*255)) if fraction is not None else (0, 0, 0)
                rgb.extend(color)
                svg.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="rgb{color}"' + (' stroke="white" stroke-width="0.03"' if (x, y) in outlined else '') + '/>')
            rows.append(rgb)
        # SVG explicitly labels the numeric temperature color scale.
        svg.append(f'<text x="0" y="{height+0.18}" font-size="0.12">{low:.4g} to {high:.4g} Cel; x-right/y-down; invalid pixels black</text></svg>')
        return [context.artifacts.add_bytes(''.join(svg).encode(), "image/svg+xml", role="derived"),
                context.artifacts.add_bytes(png(width, height, rows), "image/png", role="derived")]
