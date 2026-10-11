#!/usr/bin/env python3
"""Deterministic, entirely fabricated raw inputs for one synthetic subject.

No real recordings, proprietary scores recalculation or diagnostic thresholds.
The embedded v0.2 acquisition profiles are read by the existing raw providers.
"""
import argparse
import csv
import io
import json
import math
from pathlib import Path
import struct
import zlib

SUBJECT = "SYNTHETIC-PATIENT-001"
SESSION = "SYNTHETIC-SESSION-001"
PROTOCOL = "synthetic-patient-v1"
TIMESTAMP = "2026-01-01T12:00:00+00:00"
CLOCK = "synthetic-clock"
COMMON = {
    "schema_version": "1.0", "acquisition_mode": "synthetic",
    "processing_mode": "fresh", "origin": "synthetic_demo",
    "subject_ref": SUBJECT, "session_ref": SESSION, "protocol_id": PROTOCOL,
    "acquired_at": TIMESTAMP, "clock_domain": CLOCK,
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def png16(rows):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\0" + struct.pack(">" + str(width) + "H", *row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def generate_inputs(target):
    target = Path(target)
    for subdirectory in ("emg", "lidar", "nociception"):
        (target / subdirectory).mkdir(parents=True, exist_ok=True)
    anatomy = {"region": "lower_back", "side": "bilateral", "level": None, "numbering_status": "not_applicable"}
    calibration = {"status": "verified", "source": "Deterministic synthetic waveform formula; values explicitly in microvolts", "author_id": "synthetic-generator-v1"}
    emg = {
        **COMMON, "source_kind": "emg-raw-csv.v2", "csv_artifact_id": "BIND-ON-IMPORT",
        "pose": "synthetic stationary signal with a labelled gentle-flexion interval",
        "coordinate_frame": None, "axes": None, "anatomy": anatomy,
        "sample_rate_hz": 1000,
        "channels": [
            {"id": name, "column": name, "side": side, "muscle": "erector_spinae_longissimus",
             "electrode_placement": "Synthetic channel; no physical electrodes",
             "calibration": {**calibration, "uV_per_unit": 1},
             "adc_rails": {"lower": -100, "upper": 100}}
            for name, side in (("left_es", "left"), ("right_es", "right"))
        ],
        "calibration": calibration,
        "processing": {
            "detrend": "constant", "bandpass_hz": [20, 450], "butterworth_order": 4,
            "notch": None, "filter_pad_samples": 128, "edge_guard_samples": 128,
            "envelope_samples": 51, "window_samples": 512, "hop_samples": 512,
            "welch_segment_samples": 256, "welch_overlap_samples": 128,
            "timing_tolerance_fraction": 0.01,
        },
        "device": {"manufacturer": None, "model": None, "serial": None, "upstream_filtering": None},
        "export": {"format": "csv", "time_column": "time_s", "ignored_columns": []},
        "events": [{"id": "synthetic-flexion", "label": "Synthetic gentle flexion: event label only",
                    "start_s": 3, "stop_s": 6, "source": "Deterministic synthetic scenario; no clinical provocation"}],
    }
    with (target / "emg/signal.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["time_s", "left_es", "right_es"])
        for index in range(8192):
            t = index / 1000
            writer.writerow([f"{t:.3f}", f"{40 * math.sin(2 * math.pi * 125 * t):.12f}",
                             f"{20 * math.sin(2 * math.pi * 187.5 * t):.12f}"])
    write_json(target / "emg/source.json", emg)

    lidar = {
        **COMMON, "source_kind": "lidar-depth.v2", "pose": "Synthetic rectangular calibration plane, not a human body",
        "coordinate_frame": "depth-camera-optical",
        "axes": ["camera_x_right", "camera_y_down", "camera_z_forward"],
        "anatomy": {"region": "external_body_surface", "side": "unspecified", "level": None, "numbering_status": "not_applicable"},
        "calibration": {"status": "verified", "source": "Synthetic pinhole plane; 1 depth unit = 1 millimetre", "author_id": "synthetic-generator-v1", "mm_per_unit": 1},
        "depth_artifact_id": "BIND-ON-IMPORT", "camera_artifact_id": "BIND-ON-IMPORT",
        "camera_format": "depth-camera-calibration.v1",
        "device": {"manufacturer": None, "model": None, "serial": None, "sensor_modality": "synthetic"},
        "foreground": {"method": "largest_depth_connected_component_v1", "min_depth_units": 100,
                       "max_depth_units": 2000, "max_neighbor_delta_units": 50,
                       "min_component_pixels": 100, "point_grid_stride": 1},
        "anatomical_frame": {"status": "verified", "up_vector_camera": [0, -1, 0],
                             "source": "Declared synthetic plane up vector; no inferred spinal axis", "author_id": "synthetic-generator-v1"},
        "pca_axis_min_separation_ratio": 1.1,
    }
    camera = {
        "schema_version": "depth-camera-calibration.v1", "width": 64, "height": 64,
        "intrinsics": {"fx": 100, "fy": 100, "cx": 31.5, "cy": 31.5},
        "distortion": {"model": "none", "coefficients": None, "source": "Synthetic exact pinhole projection; no lens"},
        "depth_convention": "axial_z",
    }
    depth = [[0] * 64 for _ in range(64)]
    for v in range(10, 56):
        for u in range(20, 44):
            depth[v][u] = 1000
    # An isolated distractor verifies that the algorithm chooses the large component.
    for v in (1, 2):
        for u in (1, 2):
            depth[v][u] = 1000
    (target / "lidar/depth.png").write_bytes(png16(depth))
    write_json(target / "lidar/camera.json", camera)
    write_json(target / "lidar/source.json", lidar)

    noci = {
        **{key: value for key, value in COMMON.items() if key != "processing_mode"},
        "source_kind": "nociception-vendor-csv.v2", "csv_artifact_id": "BIND-ON-IMPORT",
        "vendor_profile": "medasense-pmd200-summary-four-columns.v1",
        "device": {"manufacturer": "Medasense", "model": "PMD-200", "software_version": None, "index_averaging_window_s": None},
        "clinical_context": "awake", "time_encoding": "elapsed_seconds", "sampling_period_s": 1,
        "timing_tolerance_fraction": 0.01, "missing_tokens": [""],
        "calibration": {"status": "unknown", "source": None, "author_id": None},
        "response_windows": {"baseline_s": 2, "response_s": 3},
        "source_provenance": {"source_url": None, "source_revision": None, "license_status": "synthetic", "license_identifier": None},
    }
    indices = [20, 20, 22, 42, 55, 50, 36, 26, 22]
    heart_rates = [70, 70, 71, 76, 82, 80, 76, 73, 71]
    with (target / "nociception/pmd200.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Time", "NOL index", "Heart rate", "Annotations"])
        for index, (nol, hr) in enumerate(zip(indices, heart_rates)):
            annotation = {3: "Synthetic gentle flexion", 6: "Synthetic return to rest"}.get(index, "")
            writer.writerow([index, nol, hr, annotation])
    write_json(target / "nociception/source.json", noci)

    expectations = {
        "synthetic_only": True, "subject_ref": SUBJECT, "session_ref": SESSION, "protocol_id": PROTOCOL,
        "emg": {"samples": 8192, "sample_rate_hz": 1000, "left_frequency_Hz": 125,
                "right_frequency_Hz": 187.5, "analytic_unfiltered_left_rms_uV": 40 / math.sqrt(2),
                "analytic_unfiltered_right_rms_uV": 20 / math.sqrt(2),
                "filtered_rms_tolerance_uV": 0.05,
                "meaning": "Known tones test raw ingestion, bandpass, windowing and feature extraction; not plausible clinical EMG or a diagnosis"},
        "lidar": {"largest_component_pixels": 1104, "candidate_pixels": 1108, "point_count": 1104,
                  "camera_extent_mm": [230, 450, 0], "pca_extent_mm": [450, 230, 0], "major_axis_up_angle_deg": 0,
                  "meaning": "Exact rectangular calibration plane; no spinal curvature, body posture or anatomical asymmetry inference"},
        "nociception": {"rows": 9, "index_mean": sum(indices) / len(indices), "heart_rate_mean_per_min": sum(heart_rates) / len(heart_rates),
                        "expected_quality": "unknown", "expected_event_response_delta": None,
                        "meaning": "Invented recorded-index columns, not a computed NOL algorithm; no vendor quality, awake dorsopathy inference unsupported"},
    }
    write_json(target / "expected-signals.json", expectations)
    return expectations


def generate(output_dir):
    """Parent bundle entry point: create inputs and return portable bindings."""
    output_dir = Path(output_dir)
    expected = generate_inputs(output_dir / "inputs")
    write_json(output_dir / "expected-signals.json", expected)
    (output_dir / "inputs/expected-signals.json").unlink()
    return [
        {"module_id": "emg.surface", "module_version": "0.2.0", "source_path": "inputs/emg/source.json",
         "artifacts": [{"field": "csv_artifact_id", "media_type": "text/csv", "path": "inputs/emg/signal.csv"}], "parameters": {}},
        {"module_id": "lidar.posture", "module_version": "0.2.0", "source_path": "inputs/lidar/source.json",
         "artifacts": [{"field": "depth_artifact_id", "media_type": "image/png", "path": "inputs/lidar/depth.png"},
                       {"field": "camera_artifact_id", "media_type": "application/json", "path": "inputs/lidar/camera.json"}], "parameters": {}},
        {"module_id": "nociception", "module_version": "0.2.0", "source_path": "inputs/nociception/source.json",
         "artifacts": [{"field": "csv_artifact_id", "media_type": "text/csv", "path": "inputs/nociception/pmd200.csv"}], "parameters": {}},
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    entries = generate(args.output)
    write_json(args.output / "signal-depth-entries.json", entries)
