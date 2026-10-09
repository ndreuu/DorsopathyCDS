"""Four named clinician-entered facts; no diagnosis is inferred here."""

import re

from dorsopathy_platform.api_contracts import FACT_IDS
from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope

_CONCORDANCE = "clinical.imaging_clinical_concordance"
_STUDY = "imaging.study_status"
_STATES = {"assessed", "not_assessed", "not_applicable"}
_FIELDS = {"schema_version", "source_kind", "mode", "origin", "acquired_at",
           "assessment_ref", "author", "facts"}
_MAX_SOURCE_BYTES = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)


def _object(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ContractError("Clinical source requires exactly documented fields")


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}:
        raise ContractError("Clinical source has an invalid opaque identifier")


def _read_json(ref, context):
    if ref["media_type"] != "application/json" or ref["size_bytes"] > _MAX_SOURCE_BYTES:
        raise ContractError("Clinical input requires bounded application/json artifacts")
    with context.artifacts.open(ref) as stream:
        raw = stream.read(_MAX_SOURCE_BYTES + 1)
    if len(raw) > _MAX_SOURCE_BYTES:
        raise ContractError("Clinical input exceeds the supported size")
    return loads_json(raw)


class ClinicalExamProvider:
    spec = ProviderSpec("clinical.exam", "0.1.0", "Явные оценки врача")

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != (self.spec.module_id, self.spec.module_version):
            raise ContractError("Clinical request does not match provider version")
        if request["parameters"] or not 1 <= len(request["input_artifacts"]) <= 2:
            raise ContractError("Clinical input requires source and optional MRI result, without parameters")
        source = request["input_artifacts"][0]
        if source["role"] != "source":
            raise ContractError("Clinical form must be a source artifact")
        data = _read_json(source, context)
        _object(data, _FIELDS)
        if data["schema_version"] != "1.0" or data["source_kind"] != "clinical-exam.v1":
            raise ContractError("Unsupported clinical source profile")
        if type(data["mode"]) is not str or type(data["origin"]) is not str or (data["mode"], data["origin"]) not in {
            ("synthetic_demo", "synthetic_demo"), ("clinician_input", "clinician_entered")
        }:
            raise ContractError("Clinical source mode and origin do not agree")
        _id(data["assessment_ref"])
        _object(data["author"], {"id", "role"})
        _id(data["author"]["id"])
        if data["author"]["role"] != "clinician":
            raise ContractError("Clinical author role must be clinician")
        _object(data["facts"], FACT_IDS)
        for concept, fact in data["facts"].items():
            fields = {"assessment_status", "value", "reviewed_result_id"} if concept == _CONCORDANCE else {"assessment_status", "value"}
            _object(fact, fields)
            if type(fact["assessment_status"]) is not str or fact["assessment_status"] not in _STATES:
                raise ContractError("Clinical fact has an unsupported assessment status")
            if fact["assessment_status"] != "assessed":
                if fact["value"] is not None:
                    raise ContractError("Unassessed clinical facts require null")
            elif concept == _STUDY:
                if type(fact["value"]) is not str or fact["value"] not in {"not_performed", "performed_result_available"}:
                    raise ContractError("Unsupported imaging study status")
            elif type(fact["value"]) is not bool:
                raise ContractError("Assessed clinical facts require explicit boolean values")

        study = data["facts"][_STUDY]
        concordance = data["facts"][_CONCORDANCE]
        reviewed_id = concordance["reviewed_result_id"]
        if reviewed_id is not None:
            _id(reviewed_id)
        if study["value"] == "not_performed":
            if concordance["assessment_status"] != "not_applicable" or reviewed_id is not None:
                raise ContractError("Not-performed MRI requires not-applicable concordance without a review")
        elif concordance["assessment_status"] == "not_applicable":
            raise ContractError("Concordance is not applicable only for an explicitly not-performed study")
        known_concordance = concordance["assessment_status"] == "assessed"
        if known_concordance:
            if study["value"] != "performed_result_available" or reviewed_id is None or len(request["input_artifacts"]) != 2:
                raise ContractError("Known concordance requires an available exact MRI result artifact")
        elif reviewed_id is not None:
            raise ContractError("Unassessed concordance must not claim a reviewed result")

        reviewed = None
        artifacts = list(request["input_artifacts"])
        warnings = []
        if len(artifacts) == 2:
            reviewed = validate_result(_read_json(artifacts[1], context))
            if (reviewed["module_id"], reviewed["module_version"]) != ("mri.anatomy", "0.1.0") or reviewed["status"] not in {"complete", "partial"}:
                raise ContractError("Reviewed artifact must be an available mri.anatomy result")
            if reviewed["episode_id"] != request["episode_id"]:
                raise ContractError("Reviewed MRI result belongs to a different episode")
            if known_concordance and reviewed["result_id"] != reviewed_id:
                raise ContractError("Reviewed MRI result ID does not match clinical source")
            if study["value"] == "not_performed":
                raise ContractError("Not-performed study must not include an MRI result artifact")
            origins = {item["origin"] for item in reviewed["observations"]}
            mixed_origin = (data["origin"] == "synthetic_demo" and bool(origins - {"synthetic_demo"})) or (
                data["origin"] != "synthetic_demo" and "synthetic_demo" in origins)
            if mixed_origin:
                warnings.append({"code": "mixed_origin_review", "message": "Clinical input and MRI origins differ; linked clinical evidence is not established."})

        observations = []
        for concept in sorted(FACT_IDS):
            fact = data["facts"][concept]
            evidence = [{"artifact_id": source["artifact_id"], "selector": {
                "kind": "json", "pointer": "/facts/" + concept}}]
            if concept == _CONCORDANCE and known_concordance:
                evidence.append({"artifact_id": artifacts[1]["artifact_id"], "selector": {
                    "kind": "json", "pointer": "/result_id"}})
            observations.append({
                "observation_id": observation_id_for(request["job_id"], concept),
                "concept": concept, "assessment_status": fact["assessment_status"],
                "value": fact["value"], "unit": None,
                "anatomy": {"region": "lumbar_spine", "side": "unspecified", "level": None,
                            "numbering_status": "not_applicable"},
                "quality": "usable" if fact["assessment_status"] == "assessed" else "unknown",
                "origin": data["origin"], "method": "explicit-clinician-assessment-v1",
                "acquired_at": data["acquired_at"], "coordinate_frame": None,
                "evidence_refs": evidence, "model": None,
            })
        partial = any(fact["assessment_status"] == "not_assessed" for fact in data["facts"].values())
        if partial:
            warnings.append({"code": "incomplete_clinical_assessment", "message": "Some clinical assessments were not performed or remain unknown."})
        return validate_result(result_envelope(request, observations, artifacts,
                                              "partial" if partial else "complete", warnings), request)
