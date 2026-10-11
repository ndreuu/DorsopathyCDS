"""Verified frozen clinical assessments to the four existing CDS facts."""

from copy import deepcopy
import hashlib

from dorsopathy_platform.api_contracts import canonical_bytes, opaque_id
from dorsopathy_platform.comparison import _read_json, _source_linkage, _validated_inputs
from dorsopathy_platform.contracts import validate_result

from .service import FACT_SCHEMA, STUDY_STATUSES

ADAPTER_VERSION = "0.2.0"
_STUDY = "imaging.study_status"
_CONCORDANCE = "clinical.imaging_clinical_concordance"
_SOURCE_FIELDS = {"schema_version", "source_kind", "mode", "origin", "acquired_at", "assessment_ref", "author", "facts"}


def _clinical_source(source):
    if type(source) is not dict or set(source) != _SOURCE_FIELDS or source["schema_version"] != "1.0" or source["source_kind"] != "clinical-exam.v1":
        raise ValueError("Clinical source profile mismatch")
    if (source["mode"], source["origin"]) not in {("synthetic_demo", "synthetic_demo"), ("clinician_input", "clinician_entered")}:
        raise ValueError("Clinical source origin mismatch")
    opaque_id(source["assessment_ref"])
    author = source["author"]
    if type(author) is not dict or set(author) != {"id", "role"} or author["role"] != "clinician":
        raise ValueError("Clinical author is unavailable")
    opaque_id(author["id"])
    if type(source["facts"]) is not dict or set(source["facts"]) != set(FACT_SCHEMA):
        raise ValueError("Clinical facts are incomplete")
    for concept, fact in source["facts"].items():
        fields = {"assessment_status", "value", "reviewed_result_id"} if concept == _CONCORDANCE else {"assessment_status", "value"}
        if type(fact) is not dict or set(fact) != fields or type(fact["assessment_status"]) is not str or fact["assessment_status"] not in {"assessed", "not_assessed", "not_applicable"}:
            raise ValueError("Clinical fact profile mismatch")
        if fact["assessment_status"] == "assessed":
            if type(fact["value"]) is not FACT_SCHEMA[concept] or concept == _STUDY and fact["value"] not in STUDY_STATUSES:
                raise ValueError("Clinical fact type mismatch")
        elif fact["value"] is not None:
            raise ValueError("Unassessed fact has a value")
    concordance = source["facts"][_CONCORDANCE]
    reviewed = concordance["reviewed_result_id"]
    if reviewed is not None:
        opaque_id(reviewed)
    if concordance["assessment_status"] == "assessed":
        if source["facts"][_STUDY]["value"] != "performed_result_available" or reviewed is None:
            raise ValueError("Known concordance has no available reviewed result")
    elif reviewed is not None:
        raise ValueError("Unassessed concordance has a review ID")
    not_performed = source["facts"][_STUDY]["value"] == "not_performed"
    if not_performed != (concordance["assessment_status"] == "not_applicable"):
        raise ValueError("Not-applicable concordance conflicts with study status")
    return source


def _candidate(result, observation, selected_by_id, artifacts):
    entry = {
        "result_id": result["result_id"], "module_id": result["module_id"], "module_version": result["module_version"],
        "observation_id": observation["observation_id"], "assessment_status": observation["assessment_status"],
        "origin": observation["origin"], "acquired_at": observation["acquired_at"], "value": observation["value"],
        "assessment_ref": None, "author": None, "source_artifact_id": None, "source_sha256": None,
        "source_selector": None, "evidence_refs": deepcopy(observation["evidence_refs"]), "reviewed_result_id": None,
        "reason_codes": [],
    }
    if result["module_id"] != "clinical.exam" or result["module_version"] != "0.1.0" or result["status"] not in {"complete", "partial"}:
        entry["reason_codes"] = ["unsupported_clinical_producer"]
        return entry
    if observation["origin"] not in {"clinician_entered", "synthetic_demo"} or observation["model"] is not None or observation["unit"] is not None:
        entry["reason_codes"] = ["clinical_origin_or_method_ineligible"]
        return entry
    concept = observation["concept"]
    expected_selector = {"kind": "json", "pointer": "/facts/" + concept}
    evidence = [item for item in observation["evidence_refs"] if item["selector"] == expected_selector]
    if len(evidence) != 1:
        entry["reason_codes"] = ["exact_source_fact_evidence_missing"]
        return entry
    refs = {ref["artifact_id"]: ref for ref in result["artifacts"]}
    ref = refs[evidence[0]["artifact_id"]]
    if ref["role"] != "source":
        entry["reason_codes"] = ["clinical_form_not_source"]
        return entry
    entry.update(source_artifact_id=ref["artifact_id"], source_sha256=ref["sha256"], source_selector=deepcopy(expected_selector))
    try:
        source = _clinical_source(_read_json(ref, artifacts))
        fact = source["facts"][concept]
        entry.update(assessment_ref=source["assessment_ref"], author=deepcopy(source["author"]))
        if observation["origin"] != source["origin"] or observation["acquired_at"] != source["acquired_at"] or observation["assessment_status"] != fact["assessment_status"] or canonical_bytes(observation["value"]) != canonical_bytes(fact["value"]):
            entry["reason_codes"] = ["observation_source_mismatch"]
            return entry
        if observation["assessment_status"] != "assessed":
            entry["reason_codes"] = ["not_applicable" if observation["assessment_status"] == "not_applicable" else "clinical_assessment_missing"]
            return entry
        if observation["quality"] != "usable":
            entry["reason_codes"] = ["clinical_assessment_quality_unusable"]
            return entry
        if concept == _CONCORDANCE:
            reviewed_id = fact["reviewed_result_id"]
            entry["reviewed_result_id"] = reviewed_id
            reviewed_result = selected_by_id.get(reviewed_id)
            if reviewed_result is None or (reviewed_result["module_id"], reviewed_result["module_version"]) not in {("mri.anatomy", "0.1.0"), ("mri.pathology", "0.1.0")} or reviewed_result["status"] not in {"complete", "partial"}:
                entry["reason_codes"] = ["reviewed_mri_result_not_selected_or_available"]
                return entry
            review_evidence = [item for item in observation["evidence_refs"] if item["selector"] == {"kind": "json", "pointer": "/result_id"}]
            matched = False
            for review in review_evidence:
                try:
                    reviewed = validate_result(_read_json(refs[review["artifact_id"]], artifacts))
                    if canonical_bytes(reviewed) == canonical_bytes(reviewed_result) and reviewed["episode_id"] == result["episode_id"]:
                        matched = True
                except (ValueError, OSError, KeyError, TypeError):
                    continue
            if not matched:
                entry["reason_codes"] = ["exact_reviewed_mri_artifact_missing"]
        return entry
    except (ValueError, OSError, KeyError, TypeError):
        entry["reason_codes"] = ["clinical_source_invalid_or_unavailable"]
        return entry


def build_case(snapshot, manifest, artifacts):
    snapshot, manifest, results, refs = _validated_inputs(snapshot, manifest)
    linkage, linkage_reasons, _ = _source_linkage(results, manifest, refs, artifacts)
    selected_by_id = {result["result_id"]: result for result in results}
    candidates = {concept: [] for concept in FACT_SCHEMA}
    for result in sorted(results, key=lambda item: item["result_id"]):
        for observation in result["observations"]:
            if observation["concept"] in candidates:
                candidates[observation["concept"]].append(_candidate(result, observation, selected_by_id, artifacts))
    facts, lineage, warnings = {}, {}, []
    for concept in sorted(FACT_SCHEMA):
        values = candidates[concept]
        selection = manifest["clinical_selection"].get(concept)
        entry = {"selection": selection, "accepted": None, "candidates": deepcopy(values), "reason_codes": []}
        eligible = [value for value in values if not value["reason_codes"]]
        if selection is not None:
            matched = [value for value in values if value["observation_id"] == selection]
            if len(matched) != 1:
                entry["reason_codes"] = ["clinical_selection_missing_or_ambiguous"]
            elif matched[0]["reason_codes"]:
                entry["reason_codes"] = list(matched[0]["reason_codes"])
            else:
                entry["accepted"] = deepcopy(matched[0])
        elif len(values) > 1:
            # Several independent assessments, including unknown vs known,
            # require explicit selection; no implicit latest/majority policy.
            entry["reason_codes"] = ["clinical_assessment_conflict_requires_selection"]
        elif len(eligible) == 1:
            entry["accepted"] = deepcopy(eligible[0])
        else:
            entry["reason_codes"] = sorted({reason for value in values for reason in value["reason_codes"]}) or ["clinical_assessment_missing"]
        if linkage != "compatible":
            entry["accepted"] = None
            entry["reason_codes"] = sorted(set(entry["reason_codes"] + linkage_reasons))
        accepted = entry["accepted"]
        facts[concept] = {"value": accepted["value"] if accepted is not None else None,
                          "origin": accepted["origin"] if accepted is not None else None,
                          "assessment_ref": accepted["assessment_ref"] if accepted is not None else None}
        lineage[concept] = entry

    def clear(concept, reason):
        facts[concept] = {"value": None, "origin": None, "assessment_ref": None}
        lineage[concept]["accepted"] = None
        lineage[concept]["reason_codes"] = sorted(set(lineage[concept]["reason_codes"] + [reason]))

    mri_results = [result for result in results if result["module_id"] in {"mri.anatomy", "mri.pathology"}]
    if facts[_STUDY]["value"] == "not_performed" and mri_results:
        clear(_STUDY, "not_performed_contradicts_selected_mri")
        clear(_CONCORDANCE, "not_performed_contradicts_selected_mri")
    if facts[_CONCORDANCE]["value"] is not None and facts[_STUDY]["value"] != "performed_result_available":
        clear(_CONCORDANCE, "selected_study_status_does_not_allow_concordance")
    for concept, entry in lineage.items():
        if entry["reason_codes"]:
            warnings.append({"code": entry["reason_codes"][0], "concept": concept,
                             "message": "The clinical fact is unknown or inapplicable; inspect its frozen lineage reasons."})
    identity = hashlib.sha256((snapshot["snapshot_id"] + "\0").encode() + canonical_bytes(manifest)).hexdigest()[:32]
    return {"case_id": "case-" + identity, "population": "adult", "facts": facts}, lineage, warnings
