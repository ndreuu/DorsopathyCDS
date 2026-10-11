"""FLIR raw JPEG -> sensor counts -> Celsius -> automatic generic foreground.

ExifTool's executable argv is trusted constructor configuration, never input
parameters. File settings permit reproducible research temperature estimates;
neither those settings nor thermal thresholding establish clinical validity.
"""
import json
from pathlib import Path
import shutil
import struct
import tempfile

from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope
from .decoder import MAX_RAW_BYTES, extract_flir
from .preview import make_previews
from .profile import validate_manifest
from .radiometry import convert_counts
from .regions import METHOD, automatic_foreground

MAX_MANIFEST_BYTES = 64 * 1024


def add_json(context, value):
    return context.artifacts.add_bytes(json.dumps(value, allow_nan=False, ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":")).encode(),
                                       "application/json", role="derived")


class RawThermographyProvider:
    spec = ProviderSpec("thermography", "0.2.0", "Raw FLIR radiometry and automatic gross thermal foreground")

    def __init__(self, exiftool_command=("exiftool",)):
        if type(exiftool_command) not in (list, tuple) or not exiftool_command or any(type(part) is not str or not part or "\x00" in part for part in exiftool_command):
            raise ValueError("ExifTool requires trusted nonempty argv")
        self.exiftool_command = tuple(exiftool_command)

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != (self.spec.module_id, self.spec.module_version) or request["parameters"]:
            raise ContractError("Raw thermography requires provider 0.2.0 and no request parameters")
        refs = request["input_artifacts"]
        jpegs = [ref for ref in refs if ref["media_type"] == "image/jpeg" and ref["role"] == "source"]
        manifests = [ref for ref in refs if ref["media_type"] == "application/json" and ref["role"] == "source"]
        if len(refs) != 2 or len(jpegs) != 1 or len(manifests) != 1:
            raise ContractError("Raw thermography requires one raw JPEG and one acquisition JSON")
        jpeg, manifest = jpegs[0], manifests[0]
        if not 0 < jpeg["size_bytes"] <= MAX_RAW_BYTES or not 0 < manifest["size_bytes"] <= MAX_MANIFEST_BYTES:
            raise ContractError("Raw thermography input sizes exceed the supported profile")
        with context.artifacts.open(manifest) as stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(content) > MAX_MANIFEST_BYTES:
            raise ContractError("Raw thermography acquisition JSON exceeds its supported size")
        data = validate_manifest(loads_json(content), jpeg)
        if data["protocol_id"] != request["protocol_id"]:
            raise ContractError("Raw thermography request and acquisition protocol must match")
        try:
            with tempfile.TemporaryDirectory(prefix="dorsopathy-thermal-") as directory:
                source_path = Path(directory) / "source.jpg"
                with context.artifacts.open(jpeg) as source, source_path.open("wb") as target:
                    if source.read(2) != b"\xff\xd8":
                        raise ContractError("Raw thermography source is not a JPEG")
                    source.seek(0)
                    shutil.copyfileobj(source, target, 1024 * 1024)
                extraction = extract_flir(source_path, directory, data["count_byte_order"], self.exiftool_command)
            matrix, radiometry = convert_counts(extraction["counts"], extraction["metadata"], data["conditions"])
        except ContractError:
            result = result_envelope(request, [], refs, "failed")
            result["error"] = {"code": "raw_radiometry_unavailable", "message": "The source lacks supported, valid FLIR PNG radiometry or the trusted extraction tool is unavailable; no temperature was assessed."}
            return validate_result(result, request)
        mask, region = automatic_foreground(matrix)
        valid_roi = region["reason"] is None and data["calibration"]["status"] != "invalid"
        values = [value for row, mask_row in zip(matrix, mask) for value, included in zip(row, mask_row) if included and value is not None]
        mean = sum(values) / len(values) if valid_roi and values else None
        maximum = max(values) if valid_roi and values else None
        minimum = min(values) if valid_roi and values else None
        counts = extraction["counts"]
        count_ref = context.artifacts.add_bytes(b"".join(struct.pack("<" + str(len(row)) + "H", *row) for row in counts), "application/octet-stream", role="derived")
        sensor_ref = context.artifacts.add_bytes(extraction["sensor_png"], "image/png", role="derived")
        metadata_ref = add_json(context, {"schema_version": "1.0", "source_kind": "thermography.flir-radiometry.v1",
                                         "source_artifact_id": jpeg["artifact_id"], "file_tags": extraction["metadata"],
                                         "decoder": extraction["decoder"], "radiometry": radiometry})
        matrix_ref = add_json(context, {"schema_version": "1.0", "source_kind": "thermography.temperature-matrix.v1",
                                       "source_artifact_id": jpeg["artifact_id"], "unit": "Cel", "matrix_axes": "row_y,column_x",
                                       "coordinate_frame": data["coordinate_frame"], "temperature_matrix": matrix,
                                       "invalid_pixels": "null", "calibration_status": data["calibration"]["status"]})
        heat, mask_png, svg, scale = make_previews(matrix, mask)
        heat_ref = context.artifacts.add_bytes(heat, "image/png", role="derived")
        mask_ref = context.artifacts.add_bytes(mask_png, "image/png", role="derived")
        svg_ref = context.artifacts.add_bytes(svg, "image/svg+xml", role="derived")
        quality = "unreliable" if data["calibration"]["status"] == "invalid" or radiometry["invalid_pixel_count"] else "unknown"
        summary = {
            "schema_version": "1.0", "source_kind": "thermography.raw-measurements.v1", "processing": "fresh",
            "source_artifact_id": jpeg["artifact_id"], "manifest_artifact_id": manifest["artifact_id"],
            "source_provenance": data["source_provenance"], "acquired_at": data["acquired_at"],
            "file_acquired_at": extraction["metadata"].get("FLIR:DateTimeOriginal"),
            "subject_ref": data["subject_ref"], "session_ref": data["session_ref"], "protocol_id": data["protocol_id"],
            "pose": data["pose"], "clock_domain": data["clock_domain"], "coordinate_frame": data["coordinate_frame"],
            "matrix_axes": "row_y,column_x", "matrix_shape_yx": [len(matrix), len(matrix[0])], "unit": "Cel",
            "sensor_counts": {"artifact_id": count_ref["artifact_id"], "dtype": "uint16", "byte_order": "little",
                              "embedded_png_count_byte_order": data["count_byte_order"], "sensor_png_artifact_id": sensor_ref["artifact_id"]},
            "temperature_matrix_artifact_id": matrix_ref["artifact_id"], "radiometry_artifact_id": metadata_ref["artifact_id"],
            "mask_artifact_id": mask_ref["artifact_id"], "heatmap_artifact_id": heat_ref["artifact_id"], "heatmap_scale": scale,
            "calibration": data["calibration"], "radiometry": radiometry, "region": region,
            "statistics": {"mean_Cel": mean, "min_Cel": minimum, "max_Cel": maximum,
                           "valid_pixel_count": len(values), "reason": "invalid_calibration" if data["calibration"]["status"] == "invalid" else region["reason"]},
            "left_right_pair": {"delta_K": None, "reason": "no_confirmed_anatomical_pair_or_orientation"},
            "quality": quality, "quality_limit": "Thermal foreground has no independent skin/body segmentation validation; file settings and temperature are research estimates, not a clinical classification.",
            "comparison_metadata": {"schema_version": "1.0", "subject_ref": data["subject_ref"], "session_ref": data["session_ref"],
                "protocol_id": data["protocol_id"], "linkage_evidence_refs": [],
                "acquired_interval": {"start": data["acquired_at"], "end": data["acquired_at"], "clock_domain": data["clock_domain"]},
                "event_id": None, "pose_id": data["pose"], "calibration_status": data["calibration"]["status"],
                "calibration_evidence_refs": [{"artifact_id": manifest["artifact_id"], "selector": {"kind": "json", "pointer": "/calibration"}}] if data["calibration"]["status"] == "verified" else [],
                "frame": {"id": data["coordinate_frame"], "units": "px", "axes": ["pixel-x-right", "pixel-y-down"]}, "transform_refs": []},
        }
        summary_ref = add_json(context, summary)
        evidence = [
            {"artifact_id": jpeg["artifact_id"], "selector": {"kind": "flir_radiometric_frame", "frame_index": 0}},
            {"artifact_id": manifest["artifact_id"], "selector": {"kind": "json", "pointer": "/conditions"}},
            {"artifact_id": metadata_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/radiometry"}},
            {"artifact_id": matrix_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/temperature_matrix", "frame_index": 0}},
            {"artifact_id": mask_ref["artifact_id"], "selector": {"kind": "image_mask", "frame_index": 0, "roi_id": region["id"], "coordinate_frame": data["coordinate_frame"]}},
            {"artifact_id": svg_ref["artifact_id"], "selector": {"kind": "image", "frame_index": 0}},
        ]
        observations = []
        for concept, value, unit, statistic in (("thermal.roi_mean_temperature", mean, "Cel", "mean"),
                                                ("thermal.roi_max_temperature", maximum, "Cel", "max"),
                                                ("thermal.left_right_delta", None, "K", "left_minus_right")):
            observations.append({"observation_id": observation_id_for(request["job_id"], concept, region["id"]),
                "concept": concept, "assessment_status": "assessed" if value is not None else "not_assessed", "value": value, "unit": unit,
                "anatomy": {"region": region["region"], "side": "unspecified", "level": None, "numbering_status": "not_applicable"},
                "quality": quality, "origin": data["origin"], "method": "flir_file_specific_planck_" + METHOD + "_" + statistic,
                "evidence_refs": evidence + [{"artifact_id": summary_ref["artifact_id"], "selector": {"kind": "measurement", "roi_id": region["id"], "statistic": statistic, "frame_index": 0}}],
                "model": None, "acquired_at": data["acquired_at"], "coordinate_frame": data["coordinate_frame"]})
        warnings = [
            {"code": "foreground_anatomy_unvalidated", "message": "The automatic region is gross thermal foreground. Skin identity, lumbar level and anatomical side are unvalidated; temperature is not a diagnosis."},
            {"code": "anatomical_pair_unassessed", "message": "Left/right temperature difference was not assessed because no comparable anatomical pair and orientation were confirmed."},
        ]
        if data["conditions"]["status"] != "independently_verified" or data["calibration"]["status"] != "verified":
            warnings.append({"code": "physical_accuracy_unknown", "message": "File-embedded radiometry records camera settings; actual physical conditions and independent temperature accuracy remain unknown."})
        if not valid_roi or radiometry["invalid_pixel_count"]:
            warnings.append({"code": "raw_temperature_qc_incomplete", "message": "Calibration, foreground coverage or invalid sensor pixels limit these measurements; missing values remain unassessed."})
        if data["source_provenance"]["license_status"] == "unknown":
            warnings.append({"code": "source_rights_unknown", "message": "The source license is unknown; this run records a local research study without asserting redistribution rights."})
        result = result_envelope(request, observations, [*refs, count_ref, sensor_ref, metadata_ref, matrix_ref, heat_ref, mask_ref, svg_ref, summary_ref], "partial", warnings)
        # Content-addressed identical PNGs (e.g. an empty mask and black image)
        # may share a reference. Declare every identity once, consistently.
        result["artifacts"] = list({ref["artifact_id"]: ref for ref in result["artifacts"]}.values())
        return validate_result(result, request)
