"""Synthetic pixels pass the same geometry and mask measurements as replay."""

def synthetic_source():
    image = [[float(row * 12 + column) for column in range(12)] for row in range(12)]
    mask = [[int(4 <= row <= 6 and 4 <= column <= 7) for column in range(12)] for row in range(12)]
    return {
        "schema_version": "1.0", "source_kind": "mri.source-grid.v1",
        "mode": "synthetic_demo", "processing": "fresh", "acquired_at": None,
        "source": {"format": "synthetic_grid", "artifact_id": None, "sha256": None},
        "registration_status": "unknown", "model": None, "comparison_metadata": None,
        "provenance": {"adapter_version": "0.1.0", "inference": "not_run"},
        "frames": [{"frame_index": 0, "frame_kind": "synthetic_native_plane",
            "geometry": {"shape_rc": [12, 12], "pixel_spacing_rc_mm": [0.8, 0.5],
                "origin_lps": [10.0, 20.0, 30.0], "row_direction_lps": [0.0, 0.8, 0.6],
                "column_direction_lps": [1.0, 0.0, 0.0]},
            "image": image, "mask": mask, "image_artifact_id": None, "mask_artifact_id": None,
            "mask_kind": "anatomy", "structure": "spinal_canal",
            "measurements": [{"measurement_id": "synthetic-canal-L4-L5", "concept": "spine.canal.cross_sectional_area",
                "level": "L4-L5", "numbering_status": "needs_confirmation", "assessment_status": "assessed",
                "quality": "usable", "method": "binary-mask-native-plane-pixel-count",
                "reason": None, "line": None}]}], "volume_measurements": [], "warnings": [],
    }
