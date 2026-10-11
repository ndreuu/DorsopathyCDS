"""Deterministic gross thermal foreground; no anatomical/skin classifier."""
from collections import deque
import math

METHOD = "gross_thermal_foreground_otsu256_largest4connected_v1"


def automatic_foreground(matrix):
    height, width = len(matrix), len(matrix[0])
    valid = [value for row in matrix for value in row if value is not None]
    low, high = min(valid), max(valid)
    empty = [[False] * width for _ in range(height)]
    description = {"id": "gross-thermal-foreground", "method": METHOD,
                   "region": "gross_thermal_foreground", "side": "unspecified", "level": None,
                   "anatomical_localization": "not_assessed", "validated_for_skin_or_body": False,
                   "histogram_bins": 256, "connectivity": 4, "mask_value": 1,
                   "tie_break": "lowest_threshold_then_first_component_in_row_major_order"}
    if high - low <= 1e-12:
        return empty, {**description, "reason": "constant_temperature_frame", "pixel_count": 0,
                       "threshold_Cel": None, "coverage_fraction": 0, "bounding_box_xyxy": None}
    bins = [0] * 256
    index_matrix = []
    for row in matrix:
        indices = [min(255, int((value - low) / (high - low) * 255)) if value is not None else None for value in row]
        index_matrix.append(indices)
        for index in indices:
            if index is not None:
                bins[index] += 1
    total, total_sum = len(valid), sum(index * count for index, count in enumerate(bins))
    weight, running_sum, best, threshold_bin = 0, 0, -1, 0
    for index in range(255):
        weight += bins[index]
        running_sum += index * bins[index]
        if weight and weight < total:
            between = weight * (total - weight) * (running_sum / weight - (total_sum - running_sum) / (total - weight)) ** 2
            if between > best:
                best, threshold_bin = between, index
    candidates = bytearray(int(value is not None and value > threshold_bin) for row in index_matrix for value in row)
    largest = []
    for seed in range(width * height):
        if not candidates[seed]:
            continue
        candidates[seed] = 0
        queue, component = deque([seed]), []
        while queue:
            position = queue.popleft()
            component.append(position)
            x, y = position % width, position // width
            neighbors = []
            if x > 0: neighbors.append(position - 1)
            if x + 1 < width: neighbors.append(position + 1)
            if y > 0: neighbors.append(position - width)
            if y + 1 < height: neighbors.append(position + width)
            for neighbor in neighbors:
                if candidates[neighbor]:
                    candidates[neighbor] = 0
                    queue.append(neighbor)
        if len(component) > len(largest):
            largest = component
    fraction = len(largest) / (width * height)
    reason = "insufficient_foreground_coverage" if len(largest) < max(16, math.ceil(width * height * .01)) else "foreground_covers_nearly_entire_frame" if fraction > .95 else None
    mask = empty
    for position in largest:
        mask[position // width][position % width] = True
    xs, ys = [position % width for position in largest], [position // width for position in largest]
    bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1] if largest else None
    border_count = sum(x in (0, width - 1) or y in (0, height - 1) for x, y in zip(xs, ys))
    return mask, {**description, "reason": reason, "threshold_Cel": low + threshold_bin / 255 * (high - low),
                  "threshold_bin": threshold_bin, "pixel_count": len(largest), "coverage_fraction": fraction,
                  "bounding_box_xyxy": bbox, "border_pixel_count": border_count,
                  "anatomical_pair": None, "orientation_confirmed": False}
