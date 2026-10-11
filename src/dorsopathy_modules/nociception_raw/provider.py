"""Documented PMD-200 CSV -> source-aligned index/HR series, QC and events.

This imports indices already computed by the device. It does not reconstruct
NOL/ANI from ECG, PPG or generic autonomic metrics. A real export fixture is
still needed to validate the documented-field adapter against vendor bytes.
"""
import json
import math

from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope
from .importer import MAX_CSV_BYTES, import_csv
from .preview import plot
from .profile import FDA_URL, HEADERS, MANUAL_URL, validate_manifest

MAX_MANIFEST_BYTES = 64 * 1024


def _json(context, value):
    return context.artifacts.add_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                                 separators=(",", ":")).encode(), "application/json", role="derived")


def _statistics(rows, qc, field, calibration):
    reasons = []
    if any(row[field] is None for row in rows):
        reasons.append("missing_or_invalid_recorded_value")
    if qc["missing_or_invalid_time_count"]:
        reasons.append("missing_or_invalid_recorded_time")
    if qc["duplicate_timestamp_count"] or qc["nonmonotonic_timestamp_count"]:
        reasons.append("duplicate_or_nonmonotonic_time")
    if qc["irregular_interval_count"]:
        reasons.append("gap_or_irregular_cadence")
    if qc["clock_conflict"] or any("mixed_timezone_encoding" in row["qc_reasons"] or "negative_relative_time" in row["qc_reasons"] or "absolute_time_overflow" in row["qc_reasons"] for row in rows):
        reasons.append("source_clock_conflict")
    if calibration["status"] == "invalid":
        reasons.append("invalid_device_calibration")
    values = [row[field] for row in rows if row[field] is not None]
    return {"mean": math.fsum(values) / len(values) if values and not reasons else None,
            "min": min(values) if values and not reasons else None,
            "max": max(values) if values and not reasons else None,
            "valid_value_count": len(values), "source_row_count": len(rows), "qc_reasons": reasons,
            "aggregation": "unweighted_mean_of_all_recorded_samples_no_interpolation",
            "physiological_reliability": "unknown_quality_not_exported"}


class RawNociceptionProvider:
    spec = ProviderSpec("nociception", "0.2.0", "Documented PMD-200 CSV fields, recorded events and explicit QC")

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != (self.spec.module_id, self.spec.module_version) or request["parameters"]:
            raise ContractError("Raw nociception requires provider 0.2.0 and no request parameters")
        refs = request["input_artifacts"]
        csvs = [ref for ref in refs if ref["role"] == "source" and ref["media_type"] == "text/csv"]
        manifests = [ref for ref in refs if ref["role"] == "source" and ref["media_type"] == "application/json"]
        if len(refs) != 2 or len(csvs) != 1 or len(manifests) != 1:
            raise ContractError("Raw nociception requires one vendor CSV and one acquisition JSON")
        source, manifest = csvs[0], manifests[0]
        if not 0 < source["size_bytes"] <= MAX_CSV_BYTES or not 0 < manifest["size_bytes"] <= MAX_MANIFEST_BYTES:
            raise ContractError("Raw nociception inputs exceed the supported size profile")
        with context.artifacts.open(manifest) as stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(content) > MAX_MANIFEST_BYTES:
            raise ContractError("Raw nociception acquisition JSON exceeds its supported size")
        data = validate_manifest(loads_json(content), source)
        if data["protocol_id"] != request["protocol_id"]:
            raise ContractError("Nociception request and acquisition protocol must match")
        try:
            with context.artifacts.open(source) as stream:
                rows, events, qc = import_csv(stream, data)
        except ContractError:
            result = result_envelope(request, [], refs, "failed")
            result["error"] = {"code": "vendor_csv_unsupported", "message": "The file does not match the supported documented PMD-200 CSV fields and explicit timestamp dialect; no index was assessed."}
            return validate_result(result, request)
        index_stats = _statistics(rows, qc, "nol_index", data["calibration"])
        heart_stats = _statistics(rows, qc, "heart_rate_per_min", data["calibration"])
        canonical = {
            "schema_version": "1.0", "source_kind": "nociception.raw-series.v2", "source_artifact_id": source["artifact_id"],
            "manifest_artifact_id": manifest["artifact_id"], "vendor_profile": data["vendor_profile"], "columns": list(HEADERS),
            "device": data["device"], "origin": data["origin"], "clinical_context": data["clinical_context"],
            "time_encoding": data["time_encoding"], "clock_domain": data["clock_domain"], "acquired_at": data["acquired_at"],
            "units": {"time_s": "s", "nol_index": "1", "heart_rate_per_min": "/min"},
            "index_scale": {"minimum": 0, "maximum": 100, "name": "NOL recorded device score"},
            "rows": rows, "events": events, "qc": qc,
            "quality_mapping": {"source": "not_present_in_documented_export", "raw_codes": None,
                                "default": "unknown", "usable_asserted": False},
            "manual_reference": {"url": MANUAL_URL, "id": "MK2U-02-708", "revision": "2", "page": 55},
            "serialization_validation": "documented_fields_only_real_vendor_export_sample_not_yet_verified",
        }
        series_ref = _json(context, canonical)
        event_responses = []
        for event in events:
            time = event["time_s"]
            event_responses.append({"event_id": event["id"], "source_row_index": event["source_row_index"],
                "baseline_interval_s": [time - data["response_windows"]["baseline_s"], time] if time is not None else None,
                "response_interval_s": [time, time + data["response_windows"]["response_s"]] if time is not None else None,
                "assessment_status": "not_assessed", "delta": None, "unit": "1",
                "reason": "quality_not_exported" if time is not None else "annotation_time_unknown",
                "clinical_interpretation": None})
        # Preserve per-row source timestamps in the series, but a partial or
        # disordered time axis cannot describe this whole-file observation.
        acquired_rows = rows if qc["temporal_integrity_complete"] and qc["global_timezone_known"] else []
        source_times = [row["time_s"] for row in rows if row["time_s"] is not None]
        # A known timestamp is not proof that clocks from other studies align.
        comparison = {"schema_version": "1.0", "subject_ref": data["subject_ref"], "session_ref": data["session_ref"],
            "protocol_id": data["protocol_id"], "linkage_evidence_refs": [],
            "acquired_interval": {"start": acquired_rows[0]["acquired_at"] if acquired_rows else None,
                                  "end": acquired_rows[-1]["acquired_at"] if acquired_rows else None,
                                  "clock_domain": data["clock_domain"]},
            "event_id": None, "pose_id": None, "calibration_status": data["calibration"]["status"],
            "calibration_evidence_refs": [{"artifact_id": manifest["artifact_id"], "selector": {"kind": "json", "pointer": "/calibration"}}] if data["calibration"]["status"] == "verified" else [],
            "frame": {"id": "pmd200-export-relative-time", "units": "s", "axes": ["time-forward"]}, "transform_refs": []}
        summary = {
            "schema_version": "1.0", "source_kind": "nociception.raw-measurements.v2", "processing": "fresh_import",
            "source_artifact_id": source["artifact_id"], "manifest_artifact_id": manifest["artifact_id"],
            "series_artifact_id": series_ref["artifact_id"], "vendor_profile": data["vendor_profile"], "device": data["device"],
            "clinical_context": data["clinical_context"], "calibration": data["calibration"], "source_provenance": data["source_provenance"],
            "recorded_interval_s": [min(source_times), max(source_times)] if source_times else None,
            "index_statistics": index_stats, "heart_rate_statistics": heart_stats, "qc": qc,
            "events": events, "event_responses": event_responses,
            "event_response_reason": "quality_not_exported" if events else "no_recorded_annotations",
            "clinical_applicability": {"eligible_for_dorsopathy_inference": False,
                "general_anaesthesia_context_declared": data["clinical_context"] == "general_anaesthesia",
                "reason": "Recorded device indices and descriptive arithmetic do not establish pain presence, absence, cause or a dorsopathy diagnosis.",
                "indications_reference": {"url": FDA_URL, "document": "DEN210022", "page": 1},
                "scope_checks_completed": False},
            "comparison_metadata": comparison,
            "serialization_validation": "real_vendor_export_sample_required",
        }
        png_data, svg_data = plot(rows, events, data["sampling_period_s"], data["timing_tolerance_fraction"], data["origin"])
        png_ref = context.artifacts.add_bytes(png_data, "image/png", role="derived")
        svg_ref = context.artifacts.add_bytes(svg_data, "image/svg+xml", role="derived")
        summary["plot_artifact_id"] = png_ref["artifact_id"]
        summary_ref = _json(context, summary)
        structural_unreliable = bool(qc["quality_unreliable_count"]) or data["calibration"]["status"] == "invalid"
        observations = []
        for concept, value, unit, key in (("nociception.device_index", index_stats["mean"], "1", "index_mean"),
                                         ("nociception.heart_rate", heart_stats["mean"], "/min", "heart_rate_mean"),
                                         ("nociception.event_response_delta", None, "1", "event_response")):
            quality = "unreliable" if structural_unreliable else "unknown"
            evidence = [
                {"artifact_id": source["artifact_id"], "selector": {"kind": "csv", "header_row": 1, "source_row_start": 0, "source_row_end_exclusive": len(rows), "columns": list(HEADERS)}},
                {"artifact_id": manifest["artifact_id"], "selector": {"kind": "json", "pointer": "/time_encoding"}},
                {"artifact_id": series_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/rows"}},
                {"artifact_id": summary_ref["artifact_id"], "selector": {"kind": "json", "pointer": {"index_mean": "/index_statistics/mean", "heart_rate_mean": "/heart_rate_statistics/mean", "event_response": "/event_responses"}[key]}},
                {"artifact_id": svg_ref["artifact_id"], "selector": {"kind": "image", "frame_index": 0}},
            ]
            observations.append({"observation_id": observation_id_for(request["job_id"], concept, key), "concept": concept,
                "assessment_status": "assessed" if value is not None else "not_assessed", "value": value, "unit": unit,
                "anatomy": {"region": "systemic_device_index", "side": "unspecified", "level": None, "numbering_status": "not_applicable"},
                "quality": quality, "origin": data["origin"],
                "method": "pmd200_documented_csv_recorded_sample_mean_v1" if key != "event_response" else "pmd200_recorded_event_response_unassessed_quality_unavailable_v1",
                "evidence_refs": evidence, "model": None, "acquired_at": acquired_rows[0]["acquired_at"] if acquired_rows else None,
                "coordinate_frame": "pmd200-export-relative-time"})
        warnings = [
            {"code": "vendor_export_quality_unknown", "message": "The documented four-column PMD-200 CSV does not export signal quality. Imported quality remains unknown and event responses were not assessed."},
            {"code": "vendor_serialization_sample_required", "message": "The adapter implements documented fields with an explicitly declared CSV/time dialect; acceptance against a legally usable real vendor export is still required."},
            {"code": "nociception_clinical_scope_unassessed", "message": "Recorded NOL and heart rate are descriptive device data; no pain intensity, absence, cause, radiculopathy or dorsopathy inference was produced."},
        ]
        if data["clinical_context"] != "general_anaesthesia":
            warnings.append({"code": "nol_context_outside_or_unknown_indications", "message": "Awake or unknown context does not establish applicability of the PMD-200 indication for adults under general anesthesia."})
        if structural_unreliable:
            warnings.append({"code": "vendor_csv_qc_incomplete", "message": "Missing/invalid values, timestamps, cadence or calibration limit the recorded series; missing data were not interpolated or replaced by zeros."})
        if data["sampling_period_s"] is None or not qc["global_timezone_known"] or data["clock_domain"] is None:
            warnings.append({"code": "vendor_clock_or_cadence_unknown", "message": "Unknown source cadence, timezone or clock prevents verified alignment with other studies; computer time was not assigned."})
        return validate_result(result_envelope(request, observations, [*refs, series_ref, png_ref, svg_ref, summary_ref], "partial", warnings), request)
