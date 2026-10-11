"""Recorded NOL samples and annotations; no interpolation or pain thresholds."""
import html
import struct
import zlib


def _png(width, height, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _line(rows, a, b, color):
    width, height = len(rows[0]) // 3, len(rows)
    x0, y0 = map(lambda value: int(round(value)), a)
    x1, y1 = map(lambda value: int(round(value)), b)
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            rows[y0][x0 * 3:x0 * 3 + 3] = bytes(color)
        if (x0, y0) == (x1, y1):
            return
        doubled = error * 2
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def plot(rows, events, period, tolerance, origin):
    width, height = 720, 280
    image = [bytearray((18, 24, 35) * width) for _ in range(height)]
    times = [row["time_s"] for row in rows if row["time_s"] is not None]
    start, stop = (min(times), max(times)) if times else (0, 1)
    span = max(stop - start, 1)
    label = "SYNTHETIC DEMO" if origin == "synthetic_demo" else "Imported device record"
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="720" height="280" viewBox="0 0 720 280">',
           '<rect width="720" height="280" fill="#121823"/>',
           f'<text x="16" y="20" fill="white" font-size="14">{label}: recorded PMD-200 NOL score [0,100]</text>',
           '<text x="16" y="38" fill="#bdc8db" font-size="12">Signal quality is absent from the documented four-column CSV</text>']
    _line(image, (35, 60), (35, 210), (109, 126, 149))
    _line(image, (35, 210), (690, 210), (109, 126, 149))
    svg.append('<path d="M35,60 V210 H690" fill="none" stroke="#6d7e95"/>')
    for value in (0, 25, 50, 75, 100):
        y = 210 - 1.5 * value
        _line(image, (31, y), (35, y), (109, 126, 149))
        svg.append(f'<text x="5" y="{y + 4:.3f}" fill="#bdc8db" font-size="10">{value}</text>')
    svg.append(f'<text x="35" y="230" fill="#bdc8db" font-size="10">{start:.6g} s</text>')
    svg.append(f'<text x="690" y="230" text-anchor="end" fill="#bdc8db" font-size="10">{stop:.6g} s</text>')
    previous = None
    for row in rows:
        time, value = row["time_s"], row["nol_index"]
        if time is None or value is None:
            previous = None
            continue
        point = (35 + 655 * (time - start) / span, 210 - 1.5 * value)
        color = (245, 181, 63) if row["quality"] == "unknown" else (243, 88, 83)
        stroke = "#f5b53f" if row["quality"] == "unknown" else "#f35853"
        # No line suggests continuity when cadence is unknown, irregular, or
        # a timestamp is duplicated/nonmonotonic. Source points are retained.
        continuous = (previous is not None and period is not None and time > previous[0]
                      and abs(time - previous[0] - period) <= period * tolerance
                      and not row["qc_reasons"] and not previous[2])
        if continuous:
            _line(image, previous[1], point, color)
            svg.append(f'<line x1="{previous[1][0]:.3f}" y1="{previous[1][1]:.3f}" x2="{point[0]:.3f}" y2="{point[1]:.3f}" stroke="{stroke}" stroke-dasharray="3,3"/>')
        _line(image, (point[0] - 1, point[1]), (point[0] + 1, point[1]), color)
        svg.append(f'<circle cx="{point[0]:.3f}" cy="{point[1]:.3f}" r="1.5" fill="{stroke}"/>')
        previous = (time, point, bool(row["qc_reasons"]))
    for index, event in enumerate(events):
        if event["time_s"] is None:
            continue
        x = 35 + 655 * (event["time_s"] - start) / span
        _line(image, (x, 50), (x, 215), (130, 157, 228))
        svg.append(f'<line x1="{x:.3f}" y1="50" x2="{x:.3f}" y2="215" stroke="#829de4" stroke-dasharray="2,3"/>')
        if index < 8:
            # XML 1.0 excludes most control characters; retain the exact
            # annotation in canonical JSON while making its preview valid.
            label = "".join(char if char in "\t\n\r" or 0x20 <= ord(char) <= 0xd7ff or 0xe000 <= ord(char) <= 0xfffd or 0x10000 <= ord(char) <= 0x10ffff else "\ufffd" for char in event["label"][:48])
            svg.append(f'<text x="{min(max(x, 35), 460):.3f}" y="{55 + 13 * (index % 3)}" fill="#a7bffc" font-size="10">{html.escape(label)}</text>')
    svg.append('<text x="16" y="250" fill="white" font-size="12">Source relative time [s]; missing values/gaps are not filled</text>')
    svg.append('<text x="16" y="270" fill="white" font-size="12">Gold: quality unknown; red: row QC unreliable; vertical lines: recorded annotations</text></svg>')
    return _png(width, height, image), "".join(svg).encode()
