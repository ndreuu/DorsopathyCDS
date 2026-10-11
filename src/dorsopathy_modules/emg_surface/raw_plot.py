"""Bounded raw/processed/envelope plots; source units stay explicitly labelled."""
import math
from xml.sax.saxutils import escape
from ._support import line, png


def plot_signals(context, rows, processed, envelopes, channels, events):
    width = 1000
    panel = 165
    height = 45 + len(channels) * panel
    pixels = [bytearray([15, 20, 30] * width) for _ in range(height)]
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="#0f141e"/>']
    def label(x, y, value):
        svg.append(f'<text x="{x}" y="{y}" fill="white" font-family="sans-serif" font-size="12">{escape(value)}</text>')
    label(20, 20, "Raw (grey), processed (cyan), rectified moving envelope (orange); sample index; gaps remain gaps")
    count = len(rows)
    # Draw at most 1000 groups per series, retaining group extrema for spikes.
    stride = max(1, math.ceil(count / 950))
    for ci, channel in enumerate(channels):
        top = 45 + ci * panel
        known = channel["calibration"]["status"] == "verified"
        factor = channel["calibration"]["uV_per_unit"] if known else 1
        raw = [row[1][ci] * factor if row[1][ci] is not None else math.nan for row in rows]
        series = [raw, processed[ci], envelopes[ci]]
        finite = [abs(float(value)) for values in series for value in values if math.isfinite(value)]
        scale = max(finite + [1e-12])
        label(20, top + 12, channel["id"] + " — " + ("uV" if known else "source units; amplitude calibration unknown"))
        for values, color, stroke in zip(series, [(105, 110, 120), (0, 220, 230), (255, 185, 0)], ["#696e78", "#00dce6", "#ffb900"]):
            previous, poly = None, []
            for start in range(0, count, stride):
                group = values[start:start + stride]
                x = 30 + 950 * start / max(1, count - 1)
                if any(not math.isfinite(float(value)) for value in group):
                    previous = None
                    if poly:
                        svg.append('<polyline points="' + " ".join(poly) + '" fill="none" stroke="' + stroke + '"/>')
                        poly = []
                    continue
                low, high = min(group), max(group)
                y1, y2 = top + 83 - 58 * low / scale, top + 83 - 58 * high / scale
                line(pixels, [x, y1], [x, y2], color)
                center = [x, (y1 + y2) / 2]
                if previous is not None:
                    line(pixels, previous, center, color)
                previous = center
                poly.append(f"{x:.2f},{center[1]:.2f}")
            if poly:
                svg.append('<polyline points="' + " ".join(poly) + '" fill="none" stroke="' + stroke + '"/>')
        label(30, top + 150, f"0 … {count - 1} samples; events: " + (", ".join(item["id"] for item in events) or "unknown"))
    svg.append("</svg>")
    return [
        context.artifacts.add_bytes("".join(svg).encode(), "image/svg+xml", role="derived"),
        context.artifacts.add_bytes(png(width, height, pixels), "image/png", role="derived"),
    ]
