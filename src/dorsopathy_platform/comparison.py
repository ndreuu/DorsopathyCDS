"""Technical comparison prerequisites; no pain/diagnosis/coherence inference."""

from copy import deepcopy
from datetime import datetime
import hashlib
from itertools import combinations
import math

from .api_contracts import canonical_bytes, opaque_id, validate_manifest
from .contracts import ContractError, loads_json, validate_result

_MAX_JSON = 1024 * 1024
_STATUSES = {"compatible", "incompatible", "unknown"}


def _validated_inputs(snapshot, manifest):
    manifest = validate_manifest(manifest)
    fields = {"schema_version", "snapshot_id", "episode_id", "result_ids", "content_sha256", "results"}
    if type(snapshot) is not dict or set(snapshot) != fields or snapshot["schema_version"] != "1.0":
        raise ContractError("Unsupported snapshot envelope")
    snapshot = loads_json(canonical_bytes(snapshot))
    content = {key: value for key, value in snapshot.items() if key not in {"snapshot_id", "content_sha256"}}
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    if snapshot["content_sha256"] != sha or snapshot["snapshot_id"] != "snapshot-" + sha:
        raise ContractError("Snapshot integrity check failed")
    if snapshot["episode_id"] != manifest["episode_id"] or type(snapshot["results"]) is not list:
        raise ContractError("Snapshot does not match manifest episode")
    results = [validate_result(result) for result in snapshot["results"]]
    ids = [result["result_id"] for result in results]
    if snapshot["result_ids"] != ids or len(set(ids)) != len(ids) or any(result["episode_id"] != snapshot["episode_id"] for result in results):
        raise ContractError("Snapshot result context check failed")
    modules = {(module["module_id"], module["module_version"]) for module in manifest["modules"]}
    reused = set(manifest["reuse_result_ids"])
    if not reused <= set(ids):
        raise ContractError("Selected reused results are absent from snapshot")
    selected = [result for result in results if (result["module_id"], result["module_version"]) in modules or result["result_id"] in reused]
    refs = {}
    for result in selected:
        for ref in result["artifacts"]:
            prior = refs.get(ref["artifact_id"])
            if prior is not None and prior != ref:
                raise ContractError("Snapshot contains conflicting artifact metadata")
            refs[ref["artifact_id"]] = ref
    return snapshot, manifest, selected, refs


def _resolve_ref(artifact_id, episode_id, refs, artifacts):
    ref = refs.get(artifact_id)
    if ref is None and callable(getattr(artifacts, "get_artifact", None)):
        ref = artifacts.get_artifact(episode_id, artifact_id)
    if ref is None:
        raise ContractError("Evidence artifact is unavailable")
    return ref


def _read_json(ref, artifacts):
    if ref["media_type"] != "application/json" or ref["size_bytes"] > _MAX_JSON:
        raise ContractError("Evidence requires bounded application/json")
    with artifacts.open(ref) as stream:
        data = stream.read(_MAX_JSON + 1)
    if len(data) != ref["size_bytes"] or len(data) > _MAX_JSON or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ContractError("Evidence artifact integrity check failed")
    return loads_json(data)


def _pointer(value, pointer):
    if type(pointer) is not str or not pointer.startswith("/"):
        raise ContractError("Unsupported JSON evidence selector")
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if type(value) is dict:
            value = value[part]
        elif type(value) is list and part.isdigit():
            value = value[int(part)]
        else:
            raise ContractError("Evidence selector cannot be resolved")
    return value


def _evidence_value(evidence, episode_id, refs, artifacts):
    if type(evidence) is not dict or set(evidence) != {"artifact_id", "selector"}:
        raise ContractError("Invalid metadata evidence reference")
    opaque_id(evidence["artifact_id"])
    selector = evidence["selector"]
    if type(selector) is not dict or set(selector) != {"kind", "pointer"} or selector["kind"] != "json":
        raise ContractError("Metadata evidence requires a JSON pointer")
    ref = _resolve_ref(evidence["artifact_id"], episode_id, refs, artifacts)
    return _pointer(_read_json(ref, artifacts), selector["pointer"])


def _evidence_list(values, episode_id, refs, artifacts):
    if type(values) is not list or not values:
        return False
    try:
        return all(_evidence_value(value, episode_id, refs, artifacts) is not None for value in values)
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        return False


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 200 and not any(ord(char) < 32 for char in value)


def _source_linkage(selected, manifest, refs, artifacts):
    """Check source bindings/known contradictions, not human identity authenticity."""
    origins = {observation["origin"] for result in selected for observation in result["observations"]}
    for ref in refs.values():
        if ref["role"] == "source" and ref["media_type"] == "application/json":
            try:
                value = _read_json(ref, artifacts)
                if type(value) is dict and _text(value.get("origin")):
                    origins.add(value["origin"])
            except (ValueError, OSError, TypeError):
                pass
    synthetic = "synthetic_demo" in origins
    real = bool(origins - {"synthetic_demo"})
    if synthetic and real:
        return "incompatible", ["mixed_synthetic_real_origins"], []
    if manifest["linkage"] == "synthetic_linked":
        if manifest["mode"] == "synthetic" and synthetic and not real:
            return "compatible", ["synthetic_fixture_linkage_only"], []
        return "unknown", ["synthetic_origin_unverified"], []
    if manifest["linkage"] != "verified_linked":
        return "unknown", ["source_linkage_unverified"], []
    if synthetic:
        return "incompatible", ["synthetic_sources_in_real_linkage"], []
    artifact_id = manifest["linkage_evidence_artifact_id"]
    evidence = [{"artifact_id": artifact_id, "selector": {"kind": "json", "pointer": "/sources"}}]
    try:
        attestation = _read_json(_resolve_ref(artifact_id, manifest["episode_id"], refs, artifacts), artifacts)
        required = {"schema_version", "source_kind", "episode_id", "subject_ref", "session_ref", "assessment_ref", "author", "sources"}
        if type(attestation) is not dict or set(attestation) != required or attestation["schema_version"] != "1.0" or attestation["source_kind"] != "linkage-attestation.v1":
            return "unknown", ["linkage_attestation_invalid"], evidence
        if attestation["episode_id"] != manifest["episode_id"]:
            return "incompatible", ["linkage_attestation_episode_mismatch"], evidence
        for key in ("subject_ref", "session_ref", "assessment_ref"):
            if not _text(attestation[key]):
                return "unknown", ["linkage_attestation_incomplete"], evidence
        author = attestation["author"]
        if type(author) is not dict or set(author) != {"id", "role"} or not _text(author["id"]) or author["role"] not in {"clinician", "research_operator"}:
            return "unknown", ["linkage_attestation_author_missing"], evidence
        bound = {}
        if type(attestation["sources"]) is not list:
            return "unknown", ["linkage_attestation_invalid"], evidence
        for source in attestation["sources"]:
            if type(source) is not dict or set(source) != {"artifact_id", "sha256"} or source["artifact_id"] in bound:
                return "unknown", ["linkage_attestation_invalid"], evidence
            bound[source["artifact_id"]] = source["sha256"]
        for result in selected:
            source_refs = [ref for ref in result["artifacts"] if ref["role"] == "source" and ref["artifact_id"] != artifact_id]
            if not source_refs:
                return "unknown", ["result_source_bindings_missing"], evidence
            for ref in source_refs:
                if ref["artifact_id"] not in bound:
                    return "unknown", ["linkage_source_binding_missing"], evidence
                if bound[ref["artifact_id"]] != ref["sha256"]:
                    return "incompatible", ["linkage_source_hash_mismatch"], evidence
        for ref in refs.values():
            if ref["media_type"] != "application/json" or ref["size_bytes"] > _MAX_JSON:
                continue
            try:
                document = _read_json(ref, artifacts)
                metadata = document.get("comparison_metadata", document) if type(document) is dict else {}
                if type(metadata) is not dict:
                    continue
                for key in ("subject_ref", "session_ref"):
                    if _text(metadata.get(key)) and metadata[key] != attestation[key]:
                        return "incompatible", ["attested_source_identity_contradiction"], evidence
            except (ValueError, OSError, TypeError):
                continue
        return "compatible", ["attested_source_bindings_only"], evidence
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        return "unknown", ["linkage_attestation_unavailable"], evidence


def _metadata_valid(metadata):
    required = {"schema_version", "subject_ref", "session_ref", "protocol_id", "linkage_evidence_refs",
                "acquired_interval", "event_id", "pose_id", "calibration_status", "frame", "transform_refs"}
    if not required <= set(metadata) or type(metadata["calibration_status"]) is not str or metadata["calibration_status"] not in {"verified", "unknown", "invalid"}:
        return False
    for key in ("subject_ref", "session_ref", "protocol_id", "event_id", "pose_id"):
        if metadata[key] is not None and not _text(metadata[key]):
            return False
    for key in ("linkage_evidence_refs", "transform_refs", "calibration_evidence_refs", "clock_evidence_refs"):
        if type(metadata.get(key, [])) is not list:
            return False
    interval, frame = metadata["acquired_interval"], metadata["frame"]
    if type(interval) is not dict or set(interval) != {"start", "end", "clock_domain"} or any(value is not None and not _text(value) for value in interval.values()):
        return False
    if type(frame) is not dict or set(frame) != {"id", "units", "axes"}:
        return False
    if any(frame[key] is not None and not _text(frame[key]) for key in ("id", "units")):
        return False
    return frame["axes"] is None or type(frame["axes"]) is list and all(_text(value) for value in frame["axes"])


def _metadata(result, artifacts):
    candidates = []
    for ref in sorted(result["artifacts"], key=lambda item: item["artifact_id"]):
        if ref["media_type"] != "application/json" or ref["size_bytes"] > _MAX_JSON:
            continue
        try:
            document = _read_json(ref, artifacts)
            metadata = document.get("comparison_metadata") if type(document) is dict else None
            if type(metadata) is dict and metadata.get("schema_version") == "1.0" and _metadata_valid(metadata):
                candidates.append((metadata, {"artifact_id": ref["artifact_id"], "selector": {"kind": "json", "pointer": "/comparison_metadata"}}))
        except (ValueError, OSError, TypeError):
            continue
    unique = {canonical_bytes(metadata) for metadata, _ in candidates}
    if len(unique) != 1:
        return None, [], "metadata_conflict" if len(unique) > 1 else "metadata_missing"
    return deepcopy(candidates[0][0]), [deepcopy(evidence) for _, evidence in candidates], None


def _operation(kind, status, reasons, evidence, **extras):
    return {"kind": kind, "status": status, "reasons": sorted(set(reasons)), "evidence_refs": deepcopy(evidence), **extras}


def _available(result, artifacts):
    refs = {ref["artifact_id"]: ref for ref in result["artifacts"]}
    try:
        used = {evidence["artifact_id"] for observation in result["observations"] for evidence in observation["evidence_refs"]}
        for artifact_id in sorted(used):
            with artifacts.open(refs[artifact_id]):
                pass
        return True
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _anatomy(result):
    items = [observation["anatomy"] for observation in result["observations"] if observation["quality"] == "usable"]
    regions = {"lower_back" if item["region"] in {"lumbar", "lumbar_spine", "lower_back"} else item["region"] for item in items}
    sides = {item["side"] for item in items}
    return regions, sides


def _semantic(left, right, evidence):
    lr, ls = _anatomy(left)
    rr, rs = _anatomy(right)
    if not lr or not rr:
        return _operation("semantic_region", "unknown", ["usable_anatomy_missing"], evidence)
    if not lr & rr:
        return _operation("semantic_region", "incompatible", ["anatomical_regions_differ"], evidence)
    if "unspecified" in ls or "unspecified" in rs:
        return _operation("semantic_region", "unknown", ["anatomical_side_unknown"], evidence)
    if not ls & rs and "bilateral" not in ls | rs:
        return _operation("semantic_region", "incompatible", ["anatomical_sides_differ"], evidence)
    return _operation("semantic_region", "compatible", ["coarse_region_only_not_registration"], evidence)


def _time(value):
    if type(value) is not str:
        raise ValueError("Unknown acquisition time")
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Unknown clock offset")
    return value


def _clock_proven(metadata, episode_id, refs, artifacts):
    values = metadata.get("clock_evidence_refs", [])
    if not _evidence_list(values, episode_id, refs, artifacts):
        return False
    try:
        for evidence in values:
            value = _evidence_value(evidence, episode_id, refs, artifacts)
            if type(value) is not dict or value.get("clock_domain") != metadata["acquired_interval"]["clock_domain"]:
                return False
            error = value.get("max_error_ms")
            anchors = value.get("sync_event_ids")
            if type(error) not in (int, float) or not math.isfinite(error) or error < 0 or type(anchors) is not list or not anchors or not all(_text(anchor) for anchor in anchors):
                return False
        return True
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _temporal(lm, rm, synthetic, episode_id, refs, artifacts, evidence):
    if lm is None or rm is None:
        return _operation("event_window", "unknown", ["acquisition_metadata_missing"], evidence)
    try:
        li, ri = lm["acquired_interval"], rm["acquired_interval"]
        ls, le, rs, re = _time(li["start"]), _time(li["end"]), _time(ri["start"]), _time(ri["end"])
        if ls > le or rs > re:
            return _operation("event_window", "incompatible", ["acquisition_interval_invalid"], evidence)
        if not _text(li.get("clock_domain")) or not _text(ri.get("clock_domain")):
            return _operation("event_window", "unknown", ["clock_domain_unknown"], evidence)
        if li["clock_domain"] != ri["clock_domain"]:
            return _operation("event_window", "unknown", ["clock_mapping_missing"], evidence)
        clock_refs = lm.get("clock_evidence_refs", []) + rm.get("clock_evidence_refs", [])
        if not synthetic and not (_clock_proven(lm, episode_id, refs, artifacts) and _clock_proven(rm, episode_id, refs, artifacts)):
            return _operation("event_window", "unknown", ["clock_accuracy_unverified"], evidence)
        evidence = evidence + clock_refs
        if le < rs or re < ls:
            return _operation("event_window", "incompatible", ["acquisition_windows_do_not_overlap"], evidence)
        if not _text(lm.get("event_id")) or not _text(rm.get("event_id")):
            return _operation("event_window", "unknown", ["event_selection_missing"], evidence)
        if lm["event_id"] != rm["event_id"]:
            return _operation("event_window", "incompatible", ["selected_events_differ"], evidence)
        if not _text(lm.get("pose_id")) or not _text(rm.get("pose_id")):
            return _operation("event_window", "unknown", ["pose_unknown"], evidence)
        if lm["pose_id"] != rm["pose_id"]:
            return _operation("event_window", "incompatible", ["selected_poses_differ"], evidence)
        return _operation("event_window", "compatible", ["declared_common_event_window_only"], evidence,
                          window={"start": max(ls, rs).isoformat(), "end": min(le, re).isoformat(), "clock_domain": li["clock_domain"], "event_id": lm["event_id"], "pose_id": lm["pose_id"]})
    except (ValueError, KeyError, TypeError):
        return _operation("event_window", "unknown", ["acquisition_time_unknown"], evidence)


def _positive_number(value):
    if type(value) in (int, float):
        return math.isfinite(value) and value > 0
    if type(value) is dict:
        return any(_positive_number(item) for item in value.values())
    if type(value) is list:
        return any(_positive_number(item) for item in value)
    return False


def _numeric_evidence(values, episode_id, refs, artifacts):
    if not _evidence_list(values, episode_id, refs, artifacts):
        return False
    try:
        return all(_positive_number(_evidence_value(value, episode_id, refs, artifacts)) for value in values)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _residual_within(values, declared_error, episode_id, refs, artifacts):
    if not _evidence_list(values, episode_id, refs, artifacts):
        return False
    try:
        for evidence in values:
            value = _evidence_value(evidence, episode_id, refs, artifacts)
            if type(value) is dict:
                value = value.get("error_value", value.get("rms_mm"))
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value > declared_error:
                return False
        return True
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _calibrated(metadata, episode_id, refs, artifacts):
    evidence = metadata.get("calibration_evidence_refs", [])
    if metadata.get("calibration_status") != "verified" or not _evidence_list(evidence, episode_id, refs, artifacts):
        return False
    try:
        return all(_positive_number(_evidence_value(item, episode_id, refs, artifacts)) for item in evidence)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _spatial(lm, rm, synthetic, episode_id, refs, artifacts, evidence):
    if lm is None or rm is None:
        return _operation("spatial_overlay", "unknown", ["frame_metadata_missing"], evidence)
    if "invalid" in {lm.get("calibration_status"), rm.get("calibration_status")}:
        return _operation("spatial_overlay", "incompatible", ["calibration_invalid"], evidence)
    if not (_calibrated(lm, episode_id, refs, artifacts) and _calibrated(rm, episode_id, refs, artifacts)):
        return _operation("spatial_overlay", "unknown", ["calibration_evidence_missing"], evidence)
    evidence += lm["calibration_evidence_refs"] + rm["calibration_evidence_refs"]
    lf, rf = lm.get("frame"), rm.get("frame")
    if type(lf) is not dict or type(rf) is not dict or not _text(lf.get("id")) or not _text(rf.get("id")):
        return _operation("spatial_overlay", "unknown", ["coordinate_frame_unknown"], evidence)
    if lf.get("units") != "mm" or rf.get("units") != "mm":
        return _operation("spatial_overlay", "unknown", ["metric_frame_or_projection_unsupported"], evidence)
    if type(lf.get("axes")) is not list or type(rf.get("axes")) is not list or len(lf["axes"]) != 3 or len(rf["axes"]) != 3:
        return _operation("spatial_overlay", "unknown", ["camera_projection_or_axes_unverified"], evidence)
    if not _text(lm.get("pose_id")) or not _text(rm.get("pose_id")):
        return _operation("spatial_overlay", "unknown", ["pose_unknown"], evidence)
    if lm["pose_id"] != rm["pose_id"]:
        return _operation("spatial_overlay", "incompatible", ["selected_poses_differ"], evidence)
    common_calibration = {item["artifact_id"] for item in lm["calibration_evidence_refs"]} & {item["artifact_id"] for item in rm["calibration_evidence_refs"]}
    if lf["id"] == rf["id"] and lf["axes"] == rf["axes"] and (synthetic or common_calibration):
        return _operation("spatial_overlay", "compatible", ["declared_shared_calibrated_frame_only"], evidence)
    transforms = lm.get("transform_refs", []) + rm.get("transform_refs", [])
    for transform_ref in transforms:
        try:
            transform = _evidence_value(transform_ref, episode_id, refs, artifacts)
            if type(transform) is not dict or transform.get("schema_version") != "1.0" or transform.get("source_kind") != "coordinate-transform.v1":
                continue
            if {transform.get("from_frame"), transform.get("to_frame")} != {lf["id"], rf["id"]}:
                continue
            if transform.get("units") != "mm" or transform.get("mapping_direction") != "from_to" or transform.get("pose_id") != lm["pose_id"] or not _text(transform.get("method")) or not _text(transform.get("version")):
                continue
            matrix = transform.get("matrix")
            if type(matrix) is not list or len(matrix) != 4 or any(type(row) is not list or len(row) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in row) for row in matrix):
                continue
            if matrix[3] != [0, 0, 0, 1]:
                continue
            a, b, c = matrix[0][:3]
            d, e, f = matrix[1][:3]
            g, h, i = matrix[2][:3]
            if abs(a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)) < 1e-12:
                continue
            validation = transform.get("validation", {})
            if type(validation) is not dict:
                continue
            error, limit = validation.get("error_value"), validation.get("max_error")
            if validation.get("status") != "verified" or validation.get("error_unit") != "mm" or type(error) not in (int, float) or type(limit) not in (int, float) or not math.isfinite(error) or not math.isfinite(limit) or error < 0 or limit < 0:
                continue
            validation_refs = validation.get("landmark_evidence_refs", []) + validation.get("validation_evidence_refs", [])
            if not (_numeric_evidence(validation.get("landmark_evidence_refs"), episode_id, refs, artifacts) and _residual_within(validation.get("validation_evidence_refs"), error, episode_id, refs, artifacts)):
                continue
            if error > limit:
                return _operation("spatial_overlay", "incompatible", ["transform_validation_error_exceeds_limit"], evidence + [transform_ref] + validation_refs)
            return _operation("spatial_overlay", "compatible", ["referenced_transform_prerequisites_only_not_resampled"], evidence + [transform_ref] + validation_refs, transform_ref=deepcopy(transform_ref))
        except (ValueError, OSError, KeyError, IndexError, TypeError):
            continue
    return _operation("spatial_overlay", "unknown", ["validated_transform_missing"], evidence)


def compare_snapshot(snapshot, manifest, artifacts):
    snapshot, manifest, results, refs = _validated_inputs(snapshot, manifest)
    linkage, linkage_reasons, linkage_evidence = _source_linkage(results, manifest, refs, artifacts)
    synthetic = linkage == "compatible" and manifest["linkage"] == "synthetic_linked"
    pairs = []
    metadata = {result["result_id"]: _metadata(result, artifacts) for result in results}
    availability = {result["result_id"]: _available(result, artifacts) for result in results}
    for left, right in combinations(sorted(results, key=lambda result: result["result_id"]), 2):
        lm, le, lreason = metadata[left["result_id"]]
        rm, re, rreason = metadata[right["result_id"]]
        observation_evidence = [item for result in (left, right) for observation in result["observations"] for item in observation["evidence_refs"]]
        evidence = le + re + linkage_evidence + observation_evidence
        reasons = list(linkage_reasons)
        if left["status"] == "failed" or right["status"] == "failed":
            operations = [_operation(kind, "unknown", ["module_result_failed"], evidence) for kind in ("semantic_region", "event_window", "spatial_overlay")]
        elif not availability[left["result_id"]] or not availability[right["result_id"]]:
            operations = [_operation(kind, "unknown", ["observation_evidence_unavailable"], evidence) for kind in ("semantic_region", "event_window", "spatial_overlay")]
        elif linkage != "compatible":
            operations = [_operation(kind, linkage, linkage_reasons, evidence) for kind in ("semantic_region", "event_window", "spatial_overlay")]
        elif lm is not None and rm is not None and _text(lm.get("subject_ref")) and _text(rm.get("subject_ref")) and lm["subject_ref"] != rm["subject_ref"]:
            operations = [_operation(kind, "incompatible", ["source_subjects_differ"], evidence) for kind in ("semantic_region", "event_window", "spatial_overlay")]
        else:
            operations = [_semantic(left, right, evidence), _temporal(lm, rm, synthetic, manifest["episode_id"], refs, artifacts, evidence), _spatial(lm, rm, synthetic, manifest["episode_id"], refs, artifacts, deepcopy(evidence))]
            if lm is not None and rm is not None and _text(lm.get("session_ref")) and _text(rm.get("session_ref")) and lm["session_ref"] != rm["session_ref"]:
                operations[1] = _operation("event_window", "incompatible", ["source_sessions_differ"], evidence)
            reasons += [reason for reason in (lreason, rreason) if reason is not None]
        statuses = {operation["status"] for operation in operations}
        status = "incompatible" if "incompatible" in statuses else "unknown" if "unknown" in statuses else "compatible"
        pairs.append({"left_result_id": left["result_id"], "right_result_id": right["result_id"], "status": status,
                      "operations": operations, "reasons": sorted(set(reasons)), "evidence_refs": deepcopy(evidence)})
    return {"schema_version": "1.0", "snapshot_id": snapshot["snapshot_id"], "pairs": pairs,
            "warnings": [{"code": "technical_comparison_only", "message": "Compatibility applies to the specified technical operation; it is not medical coherence or a diagnosis."}]}
