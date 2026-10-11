"""Official ExifTool extraction plus bounded PNG decoding from the local demo.

No JPEG/FFF binary parser is reimplemented here. The PNG unfilter algorithm
comes from ignored var/real-inputs/thermography/prepare_thermal.py and has been
changed to reject malformed, oversized and ambiguous payloads explicitly.
"""
import hashlib
from pathlib import Path
import struct
import subprocess
import zlib

from dorsopathy_platform.contracts import ContractError, loads_json
from .profile import CONDITION_TAGS

MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
RADIOMETRY_TAGS = (
    "PlanckR1", "PlanckR2", "PlanckB", "PlanckF", "PlanckO",
    "AtmosphericTransAlpha1", "AtmosphericTransAlpha2", "AtmosphericTransBeta1",
    "AtmosphericTransBeta2", "AtmosphericTransX",
)
METADATA_TAGS = tuple(CONDITION_TAGS.values()) + RADIOMETRY_TAGS + (
    "RawThermalImageWidth", "RawThermalImageHeight", "RawThermalImageType",
    "RawValueRangeMin", "RawValueRangeMax", "CameraModel", "CameraSoftware", "LensModel",
    "DateTimeOriginal", "CameraTemperatureRangeMin", "CameraTemperatureRangeMax",
)


def decode_sensor_png(payload, byte_order):
    if type(payload) is not bytes or len(payload) > MAX_RAW_BYTES or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ContractError("FLIR raw payload must be a bounded grayscale PNG")
    if byte_order not in {"little", "big"}:
        raise ContractError("FLIR count byte order must be declared")
    offset, compressed, width, height, ended = 8, bytearray(), None, None, False
    seen_idat, idat_ended = False, False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ContractError("FLIR raw PNG is truncated")
        size = struct.unpack_from(">I", payload, offset)[0]
        if size > MAX_RAW_BYTES or offset + size + 12 > len(payload):
            raise ContractError("FLIR raw PNG chunk is invalid")
        kind, data = payload[offset + 4:offset + 8], payload[offset + 8:offset + 8 + size]
        expected = struct.unpack_from(">I", payload, offset + size + 8)[0]
        if zlib.crc32(kind + data) & 0xffffffff != expected:
            raise ContractError("FLIR raw PNG checksum is invalid")
        offset += size + 12
        if width is None and kind != b"IHDR":
            raise ContractError("FLIR raw PNG must start with its header")
        if kind == b"IHDR":
            if width is not None or size != 13:
                raise ContractError("FLIR raw PNG header is ambiguous")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if (depth, color, compression, filtering, interlace) != (16, 0, 0, 0, 0):
                raise ContractError("Only noninterlaced 16-bit grayscale FLIR PNG is supported")
            if not 0 < width <= 1024 or not 0 < height <= 1024 or width * height > MAX_PIXELS:
                raise ContractError("FLIR raw PNG dimensions exceed the supported profile")
        elif kind == b"IDAT":
            if idat_ended:
                raise ContractError("FLIR raw PNG image chunks must be consecutive")
            seen_idat = True
            compressed.extend(data)
        elif kind == b"IEND":
            if size or not seen_idat or offset != len(payload):
                raise ContractError("FLIR raw PNG end is invalid")
            ended = True
            break
        else:
            if seen_idat:
                idat_ended = True
            if not kind[0] & 32:
                raise ContractError("FLIR raw PNG uses an unsupported critical chunk")
    if not ended:
        raise ContractError("FLIR raw PNG has no complete image")
    expected_size = height * (width * 2 + 1)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(bytes(compressed), expected_size + 1)
    except zlib.error:
        raise ContractError("FLIR raw PNG compression is invalid") from None
    if len(raw) != expected_size or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ContractError("FLIR raw PNG decoded size is invalid")
    stride, previous, rows = width * 2, bytearray(width * 2), []
    for y in range(height):
        filter_type = raw[y * (stride + 1)]
        if filter_type > 4:
            raise ContractError("FLIR raw PNG uses an invalid row filter")
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for index in range(stride):
            left = row[index - 2] if index >= 2 else 0
            up, corner = previous[index], previous[index - 2] if index >= 2 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            else:
                estimate = left + up - corner
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - corner))
                predictor = left if distances[0] <= min(distances[1:]) else up if distances[1] <= distances[2] else corner
            row[index] = (row[index] + predictor) & 255
        rows.append(list(struct.unpack(("<" if byte_order == "little" else ">") + str(width) + "H", row)))
        previous = row
    return rows


def _run(command, arguments, output_path, limit):
    try:
        with output_path.open("wb") as output:
            # Empty -config disables user-directory/.ExifTool_config code.
            result = subprocess.run([*command, "-config", "", *arguments], shell=False,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                    timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ContractError("Configured ExifTool extraction is unavailable") from None
    if result.returncode or output_path.stat().st_size > limit:
        raise ContractError("ExifTool extraction failed or exceeded its supported output size")


def extract_flir(source_path, work_dir, byte_order, command):
    metadata_path, sensor_path = Path(work_dir) / "exiftool.json", Path(work_dir) / "sensor.png"
    _run(command, ["-j", "-G1", "-n", "-ExifToolVersion", *["-FLIR:" + name for name in METADATA_TAGS], str(source_path)], metadata_path, 256 * 1024)
    values = loads_json(metadata_path.read_bytes())
    if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
        raise ContractError("ExifTool did not return one FLIR metadata record")
    # Keep only documented camera/radiometry tags; omit paths and camera serials.
    tags = {key: value for key, value in values[0].items() if key in {"FLIR:" + name for name in METADATA_TAGS} | {"ExifTool:ExifToolVersion"}}
    if tags.get("FLIR:RawThermalImageType") != "PNG":
        raise ContractError("Only FLIR radiometric JPEG with embedded raw PNG is supported")
    _run(command, ["-b", "-RawThermalImage", str(source_path)], sensor_path, MAX_RAW_BYTES)
    sensor = sensor_path.read_bytes()
    counts = decode_sensor_png(sensor, byte_order)
    if tags.get("FLIR:RawThermalImageWidth") != len(counts[0]) or tags.get("FLIR:RawThermalImageHeight") != len(counts):
        raise ContractError("FLIR raw PNG dimensions conflict with camera metadata")
    parser_path = Path(command[-1]).parent / "lib" / "Image" / "ExifTool" / "FLIR.pm"
    return {"counts": counts, "metadata": tags, "sensor_png": sensor,
            "decoder": {"id": "exiftool", "version": str(tags.get("ExifTool:ExifToolVersion", "unknown")),
                        "source": "https://github.com/exiftool/exiftool", "license": "Artistic-1.0-Perl OR GPL-1.0-or-later",
                        "executable_sha256": hashlib.sha256(Path(command[-1]).read_bytes()).hexdigest() if Path(command[-1]).is_file() else None,
                        "flir_parser_sha256": hashlib.sha256(parser_path.read_bytes()).hexdigest() if parser_path.is_file() else None}}
