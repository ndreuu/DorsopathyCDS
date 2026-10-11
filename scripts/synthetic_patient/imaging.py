#!/usr/bin/env python3
"""Procedural, nonpatient imaging fixtures for actual raw workers.

No photograph, vendor frame, model mask, ROI or model prediction is copied.
FLIR APP1 field offsets are taken from the installed ExifTool FLIR.pm parser;
coefficients and detector counts are invented for this mathematical phantom.
Pillow is used only to encode a visible JPEG preview, not to edit patient data.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import random
import struct
import zlib


META = {
    "subject_ref": "SYNTHETIC-PATIENT-001",
    "session_ref": "SYNTHETIC-SESSION-001",
    "protocol_id": "synthetic-patient-v1",
    "acquired_at": "2026-01-01T12:00:00+00:00",
    "clock_domain": "synthetic-utc-clock",
    "origin": "synthetic_demo",
    "acquisition_mode": "synthetic",
    "pose": "synthetic-static-phantom",
}
COEFFICIENTS = {
    "PlanckR1": 20000.0, "PlanckR2": 0.03, "PlanckB": 1500.0,
    "PlanckF": 1.0, "PlanckO": -6000,
    "AtmosphericTransAlpha1": 0.005, "AtmosphericTransAlpha2": 0.011,
    "AtmosphericTransBeta1": -0.002, "AtmosphericTransBeta2": -0.006,
    "AtmosphericTransX": 1.5,
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png(rows, width, height, depth=8, colour=0, endian="big"):
    if depth == 16:
        data = b"".join(b"\x00" + struct.pack((">" if endian == "big" else "<") + str(width) + "H", *row) for row in rows)
    else:
        data = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(data, 9)) + chunk(b"IEND", b""))


def thermal_phantom(width=160, height=192):
    """Invented temperature field, with no anatomical or inflammation label."""
    temperatures, counts = [], []
    for y in range(height):
        trow, crow = [], []
        for x in range(width):
            nx, ny = (x - width * .5) / (width * .31), (y - height * .52) / (height * .39)
            if nx * nx + ny * ny <= 1:
                temperature = 32.2 + 1.4 * (1 - nx * nx - ny * ny)
                temperature += 1.1 * math.exp(-(((x - width * .59) / 10) ** 2 + ((y - height * .62) / 13) ** 2))
            else:
                temperature = 22.0 + .15 * math.sin(x / 11) * math.cos(y / 13)
            raw = round(COEFFICIENTS["PlanckR1"] / (COEFFICIENTS["PlanckR2"] *
                        (math.exp(COEFFICIENTS["PlanckB"] / (temperature + 273.15)) - COEFFICIENTS["PlanckF"]))
                        - COEFFICIENTS["PlanckO"])
            trow.append(temperature); crow.append(raw)
        temperatures.append(trow); counts.append(crow)
    return temperatures, counts


def flir_container(counts, timestamp):
    """Minimal synthetic FFF record encoded in the real ExifTool-supported format."""
    width, height = len(counts[0]), len(counts)
    image_png = png(counts, width, height, depth=16, endian="little")
    raw_header = bytearray(32)
    struct.pack_into(">HHH", raw_header, 0, 2, width, height)
    struct.pack_into(">H", raw_header, 12, width - 1)
    struct.pack_into(">H", raw_header, 16, height - 1)
    raw = bytes(raw_header) + image_png
    camera = bytearray(0x468)
    struct.pack_into(">HHH", camera, 0, 2, width, height)
    offsets = {
        0x20: 1.0, 0x24: 0.0, 0x28: 293.15, 0x2c: 293.15,
        0x30: 293.15, 0x34: 1.0, 0x3c: 0.5,
        0x58: COEFFICIENTS["PlanckR1"], 0x5c: COEFFICIENTS["PlanckB"],
        0x60: COEFFICIENTS["PlanckF"], 0x70: COEFFICIENTS["AtmosphericTransAlpha1"],
        0x74: COEFFICIENTS["AtmosphericTransAlpha2"], 0x78: COEFFICIENTS["AtmosphericTransBeta1"],
        0x7c: COEFFICIENTS["AtmosphericTransBeta2"], 0x80: COEFFICIENTS["AtmosphericTransX"],
        0x90: 373.15, 0x94: 253.15, 0x30c: COEFFICIENTS["PlanckR2"],
    }
    for offset, value in offsets.items():
        struct.pack_into(">f", camera, offset, value)
    struct.pack_into(">i", camera, 0x308, COEFFICIENTS["PlanckO"])
    struct.pack_into(">HH", camera, 0x310, 1, 65534)
    for offset, size, value in ((0xd4, 32, "SYNTHETIC-MATH-PHANTOM"),
                                (0x114, 16, "synthetic-v1"), (0x170, 32, "NO-PHYSICAL-LENS")):
        camera[offset:offset + size] = value.encode().ljust(size, b"\x00")
    stamp = int(datetime.fromisoformat(timestamp).timestamp())
    struct.pack_into(">IIh", camera, 0x384, stamp, 0, 0)
    header = bytearray(64)
    header[:4] = b"FFF\x00"
    header[4:20] = b"SYNTHETIC-v1\x00\x00\x00\x00"
    struct.pack_into(">IIII", header, 0x14, 100, 64, 2, 3)
    directory = bytearray()
    offset = 64 + 2 * 32
    for record_type, subtype, record in ((1, 3, raw), (0x20, 1, bytes(camera))):
        directory.extend(struct.pack(">HHIIIIIII", record_type, subtype, 100, 1, offset, len(record), 0, 0, 0))
        offset += len(record)
    return bytes(header) + bytes(directory) + raw + bytes(camera), image_png


def thermal_jpeg(temperatures, counts, timestamp):
    from PIL import Image
    width, height = len(counts[0]), len(counts)
    pixels = []
    for row in temperatures:
        for temperature in row:
            u = max(0, min(1, (temperature - 21) / 14))
            pixels.extend((round(255 * u), round(180 * u * u), round(90 * (1 - u))))
    visible = Image.frombytes("RGB", (width, height), bytes(pixels))
    encoded = BytesIO(); visible.save(encoded, format="JPEG", quality=85, subsampling=0, optimize=False, progressive=False)
    jpeg = encoded.getvalue()
    fff, raw_png = flir_container(counts, timestamp)
    max_chunk = 65525
    blocks = [fff[i:i + max_chunk] for i in range(0, len(fff), max_chunk)]
    app1 = bytearray()
    for index, block in enumerate(blocks):
        payload = b"FLIR\x00\x01" + bytes((index, len(blocks) - 1)) + block
        app1.extend(b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload)
    return jpeg[:2] + bytes(app1) + jpeg[2:], raw_png


def ultrasound_phantom(width=512, height=384):
    """Procedural speckle and curved bright interfaces; not a clinical phantom."""
    rng = random.Random(18421)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            attenuation = math.exp(-y / 650)
            speckle = min(80, math.sqrt(-2 * math.log(max(1e-12, rng.random()))) * 17)
            upper = 105 + 0.00040 * (x - 256) ** 2 + 2 * math.sin(x / 50)
            lower = 232 - 0.00025 * (x - 256) ** 2 + 3 * math.sin(x / 75)
            interface = 126 * math.exp(-((y - upper) / 2.0) ** 2) + 142 * math.exp(-((y - lower) / 2.5) ** 2)
            muscle = 32 if upper < y < lower else 45
            bone = 165 * math.exp(-((x - 429) / 4) ** 2) * math.exp(-((y - 204) / 43) ** 2)
            shadow = .35 if abs(x - 429) < 11 and y > 240 else 1
            value = round(max(0, min(255, (muscle + speckle) * attenuation * shadow + interface + bone)))
            row.extend((value, value, value))
        rows.append(row)
    return png(rows, width, height, colour=2)


def generate(output_root, metadata=None):
    output_root = Path(output_root)
    meta = {**META, **(metadata or {})}
    thermal = output_root / "inputs" / "thermography"; ultrasound = output_root / "inputs" / "ultrasound"
    thermal.mkdir(parents=True, exist_ok=True); ultrasound.mkdir(parents=True, exist_ok=True)
    temperatures, counts = thermal_phantom()
    jpeg, sensor_png = thermal_jpeg(temperatures, counts, meta["acquired_at"])
    (thermal / "raw-flir.jpg").write_bytes(jpeg)
    # Ground truth is a separate QA file, never an uploaded authored result.
    write_json(thermal / "generator-truth.json", {
        "source_kind": "synthetic-generator-truth.v1", "not_an_uploaded_model_result": True,
        "generator": "generate_imaging.py", "coefficients": COEFFICIENTS,
        "count_png_sha256": hashlib.sha256(sensor_png).hexdigest(),
        "shape_yx": [len(counts), len(counts[0])],
        "temperature_field_min_Cel": min(min(r) for r in temperatures),
        "temperature_field_max_Cel": max(max(r) for r in temperatures),
        "count_min": min(min(r) for r in counts), "count_max": max(max(r) for r in counts),
        "limitations": "Invented radiometric coefficients, nominal temperatures and geometry. No camera, no patient, no inflammation label.",
    })
    thermal_source = {
        "schema_version": "1.0", "source_kind": "thermography-raw-flir.v1", **meta,
        "jpeg_artifact_id": "REPLACE_AFTER_UPLOAD", "coordinate_frame": "synthetic-native-thermal-pixels",
        "count_byte_order": "little", "calibration": {"status": "unknown", "source": None, "author_id": None},
        "conditions": {"status": "file_settings_only", "source": None, "emissivity": None,
            "distance_m": None, "reflected_Cel": None, "ambient_Cel": None, "window_Cel": None,
            "window_transmission": None, "relative_humidity_fraction": None},
        "source_provenance": {"source_url": None, "source_revision": "procedural-phantom-v1",
            "license_status": "synthetic", "license_identifier": None},
    }
    write_json(thermal / "source.json", thermal_source)
    (ultrasound / "raw-phantom.png").write_bytes(ultrasound_phantom())
    ultrasound_source = {
        "schema_version": "1.0", "source_kind": "ultrasound-raw.v1", **meta,
        "processing_mode": "fresh", "coordinate_frame": "synthetic-ultrasound-source-pixels",
        "axes": ["x-right", "y-down"],
        "anatomy": {"region": "lumbar_multifidus", "side": "unspecified", "level": None,
                    "numbering_status": "needs_confirmation"},
        "frame_artifact_id": "REPLACE_AFTER_UPLOAD", "frame_index": None,
        "structure": "lumbar_multifidus", "plane": "transverse",
        "calibration": {"status": "verified", "source": "Synthetic pixel-grid definition only: 0.15 mm per pixel; no physical or clinical calibration",
            "author_id": "synthetic-generator", "mm_per_pixel_x": .15, "mm_per_pixel_y": .15,
            "region": [0, 0, 511, 383]},
    }
    write_json(ultrasound / "source.json", ultrasound_source)
    return [
        {"module_id": "thermography", "module_version": "0.2.0",
         "source_path": "inputs/thermography/source.json", "artifacts": [
            {"field": "jpeg_artifact_id", "media_type": "image/jpeg", "path": "inputs/thermography/raw-flir.jpg"}],
         "parameters": {}},
        {"module_id": "ultrasound", "module_version": "0.2.0",
         "source_path": "inputs/ultrasound/source.json", "artifacts": [
            {"field": "frame_artifact_id", "media_type": "image/png", "path": "inputs/ultrasound/raw-phantom.png"}],
         "parameters": {}},
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "generated")
    args = parser.parse_args()
    print(json.dumps(generate(args.output), indent=2))
