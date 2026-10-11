"""Bounded grayscale PNG16 decoder; CRC, byte order and PNG filters checked."""
import struct
import zlib

from dorsopathy_platform.contracts import ContractError

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 2 * 1024 * 1024


def decode_depth_png(stream):
    data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ContractError("Depth requires bounded PNG16")
    offset, width, height = 8, None, None
    compressed = bytearray()
    seen_idat, idat_ended, ended = False, False, False
    for _ in range(4096):
        if offset + 12 > len(data):
            raise ContractError("Incomplete depth PNG")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        if size > MAX_BYTES or offset + size + 12 > len(data):
            raise ContractError("Invalid PNG chunk size")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[offset + size + 8:offset + size + 12])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ContractError("Depth PNG CRC mismatch")
        offset += size + 12
        if width is None and kind != b"IHDR":
            raise ContractError("PNG IHDR must be first")
        if kind == b"IHDR":
            if width is not None or size != 13:
                raise ContractError("Invalid duplicate PNG header")
            width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (bits, color, compression, filtering, interlace) != (16, 0, 0, 0, 0) or not 1 <= width <= 4096 or not 1 <= height <= 4096 or width * height > MAX_PIXELS:
                raise ContractError("Unsupported PNG depth format or dimensions")
        elif kind == b"IDAT":
            if idat_ended:
                raise ContractError("PNG IDAT chunks must be contiguous")
            seen_idat = True
            compressed.extend(payload)
        elif kind == b"IEND":
            if size or not seen_idat or offset != len(data):
                raise ContractError("Invalid depth PNG end")
            ended = True
            break
        else:
            if seen_idat:
                idat_ended = True
            if not kind[0] & 32:
                raise ContractError("Unsupported critical depth PNG chunk")
    if not ended:
        raise ContractError("PNG has too many chunks or no end")
    expected = height * (width * 2 + 1)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected + 1)
        if len(raw) > expected or decoder.unconsumed_tail:
            raise ContractError("Depth PNG expansion exceeds declared dimensions")
        raw += decoder.flush(expected + 1 - len(raw))
        if len(raw) != expected or not decoder.eof or decoder.unused_data:
            raise ContractError("Invalid bounded PNG zlib stream")
    except zlib.error:
        raise ContractError("Invalid depth PNG zlib data") from None
    stride, previous, rows = width * 2, bytearray(width * 2), []
    for y in range(height):
        base = y * (stride + 1)
        method = raw[base]
        if method not in range(5):
            raise ContractError("Unsupported PNG row filter")
        row = bytearray(raw[base + 1:base + stride + 1])
        for index in range(stride):
            left = row[index - 2] if index >= 2 else 0
            up = previous[index]
            corner = previous[index - 2] if index >= 2 else 0
            if method == 0:
                predictor = 0
            elif method == 1:
                predictor = left
            elif method == 2:
                predictor = up
            elif method == 3:
                predictor = (left + up) // 2
            else:
                estimate = left + up - corner
                a, b, c = abs(estimate - left), abs(estimate - up), abs(estimate - corner)
                predictor = left if a <= b and a <= c else up if b <= c else corner
            row[index] = (row[index] + predictor) & 255
        rows.append(struct.unpack(">" + str(width) + "H", row))
        previous = row
    return width, height, rows
