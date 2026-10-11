"""Exact native-pixel mask/heatmap with an explicit Celsius SVG scale."""
import struct
import zlib


def png(width, height, rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    data = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))


def make_previews(matrix, mask):
    height, width = len(matrix), len(matrix[0])
    values = [value for row in matrix for value in row if value is not None]
    low, high = min(values), max(values)
    span = max(high - low, 1e-12)
    heat_rows, mask_rows = [], []
    for y, row in enumerate(matrix):
        heat_row, mask_row = bytearray(), bytearray()
        for x, value in enumerate(row):
            fraction = (value - low) / span if value is not None else None
            color = (round(255 * fraction), round(200 * (1 - abs(2 * fraction - 1))), round(255 * (1 - fraction))) if fraction is not None else (0, 0, 0)
            boundary = mask[y][x] and (x == 0 or x == width - 1 or y == 0 or y == height - 1 or
                                      not all(mask[ny][nx] for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))))
            heat_row.extend((255, 255, 255) if boundary else color)
            mask_row.extend((255, 255, 255) if mask[y][x] else (0, 0, 0))
        heat_rows.append(heat_row)
        mask_rows.append(mask_row)
    heat, binary_mask = png(width, height, heat_rows), png(width, height, mask_rows)
    # The embedded image preserves exact native pixels. Caption and scale are
    # outside the image: no crop/resize changes the measurement coordinates.
    import base64
    display_width = max(640, width)
    display_height = round(height * display_width / width)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{display_width}" height="{display_height + 90}" viewBox="0 0 {display_width} {display_height + 90}">'
           '<rect width="100%" height="100%" fill="#141c2c"/>'
           f'<image width="{display_width}" height="{display_height}" image-rendering="pixelated" xlink:href="data:image/png;base64,{base64.b64encode(heat).decode()}"/>'
           f'<text x="12" y="{display_height + 24}" fill="white" font-size="16">{low:.3f} to {high:.3f} Cel; native x-right / y-down</text>'
           f'<text x="12" y="{display_height + 48}" fill="white" font-size="14">White contour: automatic gross thermal foreground; invalid pixels black</text>'
           f'<text x="12" y="{display_height + 72}" fill="white" font-size="14">Anatomy, skin identity and left/right pairing not validated</text></svg>')
    return heat, binary_mask, svg.encode(), {"minimum_Cel": low, "maximum_Cel": high, "invalid_color": "black", "contour_color": "white"}
