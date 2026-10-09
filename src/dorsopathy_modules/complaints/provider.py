"""Validated scalar complaints and a separate immutable external body map."""

import json
import math
import re

from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope

_MAX_SOURCE_BYTES = 1024 * 1024
_SIDES = {"left", "right", "bilateral", "midline", "unspecified"}
_STATES = {"assessed", "not_assessed", "not_applicable"}
_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
_FIELDS = {"schema_version", "source_kind", "mode", "origin", "acquired_at",
           "form_id", "form_version", "assessment_ref", "author", "anatomy",
           "answers", "bodymap"}


def _object(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ContractError("Complaints source requires exactly documented fields")


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}:
        raise ContractError("Complaints source has an invalid opaque identifier")


def _answer(value, unit, intensity=False):
    _object(value, {"assessment_status", "value", "unit"})
    status = value["assessment_status"]
    if type(status) is not str or status not in _STATES or value["unit"] != unit:
        raise ContractError("Complaints answer has an unsupported status or unit")
    number = value["value"]
    if status != "assessed":
        if number is not None:
            raise ContractError("Unassessed complaints answers require null")
    elif intensity:
        if type(number) is not int or not 0 <= number <= 10:
            raise ContractError("Pain intensity must be an integer from zero to ten")
    elif type(number) not in (int, float) or (type(number) is float and not math.isfinite(number)) or number < 0:
        raise ContractError("Pain duration must be a finite nonnegative number of days")


class ComplaintsProvider:
    spec = ProviderSpec("complaints", "0.1.0", "Жалобы пациента")

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != (self.spec.module_id, self.spec.module_version):
            raise ContractError("Complaints request does not match provider version")
        if request["parameters"] or len(request["input_artifacts"]) != 1:
            raise ContractError("Complaints requires one source artifact and no parameters")
        source = request["input_artifacts"][0]
        if source["media_type"] != "application/json" or source["role"] != "source":
            raise ContractError("Complaints requires an application/json source artifact")
        if source["size_bytes"] > _MAX_SOURCE_BYTES:
            raise ContractError("Complaints source exceeds the supported size")
        with context.artifacts.open(source) as stream:
            raw = stream.read(_MAX_SOURCE_BYTES + 1)
        if len(raw) > _MAX_SOURCE_BYTES:
            raise ContractError("Complaints source exceeds the supported size")
        data = loads_json(raw)
        _object(data, _FIELDS)
        if data["schema_version"] != "1.0" or data["source_kind"] != "complaints.v1":
            raise ContractError("Unsupported complaints source profile")
        if type(data["mode"]) is not str or type(data["origin"]) is not str or (data["mode"], data["origin"]) not in {
            ("synthetic_demo", "synthetic_demo"), ("patient_input", "patient_report")
        }:
            raise ContractError("Complaints source mode and origin do not agree")
        for field in ("form_id", "form_version", "assessment_ref"):
            _id(data[field])
        _object(data["author"], {"id", "role"})
        _id(data["author"]["id"])
        if data["author"]["role"] != "patient":
            raise ContractError("Complaints author role must be patient")
        _object(data["anatomy"], {"region", "side"})
        if data["anatomy"]["region"] != "lower_back" or type(data["anatomy"]["side"]) is not str or data["anatomy"]["side"] not in _SIDES:
            raise ContractError("Unsupported complaints external region or side")
        _object(data["answers"], {"pain_intensity", "duration"})
        _answer(data["answers"]["pain_intensity"], "{score}", intensity=True)
        _answer(data["answers"]["duration"], "d")
        bodymap = data["bodymap"]
        _object(bodymap, {"assessment_status", "selected_regions"})
        if type(bodymap["assessment_status"]) is not str or bodymap["assessment_status"] not in _STATES:
            raise ContractError("Body map has an unsupported assessment status")
        regions = bodymap["selected_regions"]
        if bodymap["assessment_status"] == "assessed":
            if type(regions) is not list:
                raise ContractError("Assessed body map requires an array")
            for region in regions:
                _id(region)
            if len(regions) != len(set(regions)):
                raise ContractError("Body map regions must be unique")
        elif regions is not None:
            raise ContractError("Unassessed body map requires null regions")

        observations = []
        for answer_id, concept in (("pain_intensity", "symptoms.pain_intensity"),
                                   ("duration", "symptoms.pain_duration")):
            answer = data["answers"][answer_id]
            observations.append({
                "observation_id": observation_id_for(request["job_id"], concept),
                "concept": concept, "assessment_status": answer["assessment_status"],
                "value": answer["value"], "unit": answer["unit"],
                "anatomy": {**data["anatomy"], "level": None, "numbering_status": "not_applicable"},
                "quality": "usable" if answer["assessment_status"] == "assessed" else "unknown",
                "origin": data["origin"], "method": "structured-patient-input-v1",
                "acquired_at": data["acquired_at"], "coordinate_frame": None,
                "evidence_refs": [{"artifact_id": source["artifact_id"], "selector": {
                    "kind": "json", "pointer": "/answers/" + answer_id}}],
                "model": None,
            })
        partial = any(answer["assessment_status"] == "not_assessed"
                      for answer in data["answers"].values()) or bodymap["assessment_status"] == "not_assessed"
        # Validate time and all wire fields before publishing any derived artifact.
        status = "partial" if partial else "complete"
        warnings = [{"code": "incomplete_complaints", "message": "Some complaints or body map answers were not assessed."}] if partial else []
        validate_result(result_envelope(request, observations, [source], status, warnings), request)
        export = {key: data[key] for key in ("schema_version", "mode", "origin", "acquired_at",
                                             "assessment_ref", "author", "anatomy", "bodymap")}
        export.update({"source_kind": "complaints.bodymap.v1", "source_artifact_id": source["artifact_id"],
                       "source_selector": {"kind": "json", "pointer": "/bodymap"}})
        map_ref = context.artifacts.add_bytes(
            json.dumps(export, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            "application/json", role="derived")
        return validate_result(result_envelope(request, observations, [source, map_ref], status, warnings), request)
