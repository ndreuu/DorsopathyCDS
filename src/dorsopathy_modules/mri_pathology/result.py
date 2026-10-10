"""Convert SpineNet categories into evidenced, non-diagnostic observations.

This module neither runs inference nor verifies model bytes. The isolated
worker must hash the checkpoint and register its sidecar and image artifacts
before calling this adapter. Class probabilities stay in that sidecar: they
are classifier outputs, not clinical probabilities.
"""

from __future__ import annotations

import math
from typing import Any

from dorsopathy_platform.contracts import ContractError, validate_request, validate_result
from dorsopathy_platform.results import observation_id_for, result_envelope


_LEVELS = ("L1-L2", "L2-L3", "L3-L4", "L4-L5", "L5-S1")
_COORDINATE_FRAME = "SpineNet-canonical-sagittal-pixels"
_HEADS = {
    "CentralCanalStenosis": ("spine.central_stenosis.grade", 1, ("none", "mild", "moderate", "severe"), "midline"),
    "Herniation": ("spine.disc.herniation.present", 0, (False, True), "unspecified"),
}
_PAYLOAD_FIELDS = {"schema_version", "processing", "source", "model", "levels", "acquired_at", "provenance"}
_EVIDENCE_FIELDS = {"source_frame_index", "source_png_artifact_id", "ivd_png_artifact_id", "model_input_artifact_id"}


def _fail(path: str, message: str) -> None:
    # Do not put supplied source metadata or arbitrary keys into error messages.
    raise ContractError("{}: {}".format(path, message))


def _object(value: Any, fields: set[str], path: str) -> dict:
    if type(value) is not dict or any(type(key) is not str for key in value) or set(value) != fields:
        _fail(path, "requires exactly the documented object fields")
    return value


def _finite_json(value: Any, active: set[int] | None = None, depth: int = 0) -> None:
    if depth > 64:
        _fail("spinenet", "JSON nesting exceeds the supported limit")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        _fail("spinenet", "requires finite JSON values")
    if type(value) is dict and any(type(key) is not str for key in value):
        _fail("spinenet", "JSON objects require string keys")
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        _fail("spinenet", "cyclic values are not JSON")
    active.add(identity)
    try:
        for item in value.values() if type(value) is dict else value:
            _finite_json(item, active, depth + 1)
    finally:
        active.remove(identity)


def _scores(value: Any, class_count: int, path: str) -> list:
    if type(value) is not list or len(value) != class_count:
        _fail(path, "requires the documented number of class scores")
    if any(type(score) not in (int, float) or not 0 <= score <= 1 or not math.isfinite(score) for score in value):
        _fail(path, "class scores must be finite numbers between zero and one")
    if not math.isclose(sum(value), 1.0, rel_tol=0.0, abs_tol=1e-6):
        _fail(path, "class scores must sum to one")
    return value


def _derived_reference(artifact_id: Any, refs: dict, media_types: set[str], path: str) -> dict:
    if type(artifact_id) is not str or artifact_id not in refs:
        _fail(path, "must reference a declared derived artifact")
    reference = refs[artifact_id]
    if reference["role"] != "derived" or reference["media_type"] not in media_types:
        _fail(path, "requires the documented derived artifact type")
    return reference


def _evidence(source: dict, sidecar: dict, item: dict | None, position: int | None, head: str) -> list[dict]:
    evidence = [
        {"artifact_id": source["artifact_id"], "selector": {"kind": "source_volume"}},
        {"artifact_id": sidecar["artifact_id"], "selector": {
            "kind": "json", "pointer": "/levels" if position is None else "/levels/{}/grades/{}".format(position, head),
        }},
    ]
    if item is None:
        return evidence
    links = item["evidence"]
    for key, content in (("source_png_artifact_id", "vertebral_context"), ("ivd_png_artifact_id", "ivd_crop")):
        evidence.append({"artifact_id": links[key], "selector": {
            "kind": "image", "frame_index": links["source_frame_index"],
            "coordinate_frame": _COORDINATE_FRAME, "content": content,
        }})
    evidence.append({"artifact_id": links["model_input_artifact_id"], "selector": {
        "kind": "model_input", "coordinate_frame": _COORDINATE_FRAME,
    }})
    return evidence


def build_pathology_result(
    payload: dict, request: dict, sidecar_reference: dict, artifacts: list[dict],
) -> dict:
    """Validate a fresh worker payload and return a validated partial result.

    ``artifacts`` declares the JSON sidecar, source-context PNG, IVD PNG and
    exact model-input tensor artifacts already stored by the worker. This
    function does not open artifacts or infer whether pixels are synthetic;
    trusted worker provenance must explicitly declare acquisition as
    ``recorded`` or ``synthetic``. A synthetic input always stays synthetic,
    including when inference was freshly run on it.
    """
    request = validate_request(request)
    if (request["module_id"], request["module_version"]) != ("mri.pathology", "0.1.0"):
        _fail("request", "requires mri.pathology module version 0.1.0")
    if request["parameters"] != {"processing_mode": "live"} or len(request["input_artifacts"]) != 1:
        _fail("request", "requires one input artifact and processing_mode live")
    source = request["input_artifacts"][0]
    if source["role"] != "source":
        _fail("request.input_artifacts", "requires a source volume artifact")

    _object(payload, _PAYLOAD_FIELDS, "spinenet")
    _finite_json(payload)
    if payload["schema_version"] != "mri.spinenet.v1" or payload["processing"] != "fresh":
        _fail("spinenet", "requires the documented fresh SpineNet schema")
    identity = _object(payload["source"], {"artifact_id", "sha256"}, "spinenet.source")
    if identity != {"artifact_id": source["artifact_id"], "sha256": source["sha256"]}:
        _fail("spinenet.source", "must match the exact request source identity")
    model = _object(payload["model"], {"id", "version", "weights_sha256"}, "spinenet.model")
    if (model["id"], model["version"]) != ("spinenet-v2-grading", "5e96ac54"):
        _fail("spinenet.model", "requires the documented grading model and version")
    provenance = payload["provenance"]
    if type(provenance) is not dict or provenance.get("acquisition") not in ("recorded", "synthetic"):
        _fail("spinenet.provenance", "requires explicit recorded or synthetic acquisition")
    origin = "synthetic_demo" if provenance["acquisition"] == "synthetic" else "model_predicted"

    # The platform checks all declared refs, duplicates and shared input IDs.
    artifact_result = validate_result(result_envelope(request, [], artifacts, "partial"), request)
    refs = {reference["artifact_id"]: reference for reference in artifact_result["artifacts"]}
    if type(sidecar_reference) is not dict or type(sidecar_reference.get("artifact_id")) is not str:
        _fail("sidecar_reference", "requires a declared JSON artifact reference")
    sidecar = _derived_reference(sidecar_reference["artifact_id"], refs, {"application/json"}, "sidecar_reference")
    if sidecar != sidecar_reference:
        _fail("sidecar_reference", "must match its complete declared artifact reference")

    if type(payload["levels"]) is not list:
        _fail("spinenet.levels", "requires a JSON array")
    levels = {}
    for position, item in enumerate(payload["levels"]):
        path = "spinenet.levels[{}]".format(position)
        _object(item, {"level", "grades", "scores", "evidence"}, path)
        level = item["level"]
        if type(level) is not str or level not in _LEVELS:
            _fail(path + ".level", "requires a supported lumbar disc level")
        if level in levels:
            _fail("spinenet.levels", "level outputs must be unique")
        grades = _object(item["grades"], set(_HEADS), path + ".grades")
        scores = _object(item["scores"], set(_HEADS), path + ".scores")
        for head, (_, class_offset, categories, _) in _HEADS.items():
            grade = grades[head]
            if type(grade) is not int or not class_offset <= grade < class_offset + len(categories):
                _fail(path + ".grades." + head, "requires a documented integer class code")
            vector = _scores(scores[head], len(categories), path + ".scores." + head)
            # Python max retains the first class on ties, matching torch.argmax.
            if grade != class_offset + max(range(len(vector)), key=vector.__getitem__):
                _fail(path + ".grades." + head, "must match the first maximum-score class")
        links = _object(item["evidence"], _EVIDENCE_FIELDS, path + ".evidence")
        frame = links["source_frame_index"]
        if type(frame) is not int or frame < 0:
            _fail(path + ".evidence.source_frame_index", "requires a non-negative integer frame index")
        for key in ("source_png_artifact_id", "ivd_png_artifact_id"):
            _derived_reference(links[key], refs, {"image/png"}, path + ".evidence." + key)
        _derived_reference(links["model_input_artifact_id"], refs, {"application/x-npy", "application/octet-stream"}, path + ".evidence.model_input_artifact_id")
        levels[level] = (position, item)

    warnings = [
        {"code": "model_category_not_diagnosis", "message": "SpineNet categories require independent review and do not establish a diagnosis; class scores are not clinical probabilities."},
        {"code": "unsupported_task_coverage", "message": "Only central canal stenosis and disc herniation categories are assessed; other pathology tasks remain unassessed."},
        {"code": "numbering_needs_confirmation", "message": "All SpineNet disc-level numbering requires confirmation."},
        {"code": "context_image_not_pathology_contour", "message": "Source PNGs show vertebral context and IVD PNGs show model crops; neither is a pathology contour."},
    ]
    if origin == "synthetic_demo":
        warnings.append({"code": "synthetic_input", "message": "Fresh inference used synthetic input; all observations retain synthetic_demo origin."})
    observations = []
    for level in _LEVELS:
        position, item = levels.get(level, (None, None))
        if item is None:
            warnings.append({"code": "level_not_assessed", "message": "{} has no SpineNet output; both categories remain not assessed because the level was not detected.".format(level)})
        for head, (concept, class_offset, categories, side) in _HEADS.items():
            observations.append({
                "observation_id": observation_id_for(request["job_id"], concept, level),
                "concept": concept, "assessment_status": "not_assessed" if item is None else "assessed",
                "value": None if item is None else categories[item["grades"][head] - class_offset], "unit": None,
                "anatomy": {"region": "lumbar_spine", "side": side, "level": level, "numbering_status": "needs_confirmation"},
                "quality": "unknown", "origin": origin, "method": "SpineNet-v2-grading-category",
                "evidence_refs": _evidence(source, sidecar, item, position, head), "model": model,
                "acquired_at": payload["acquired_at"], "coordinate_frame": _COORDINATE_FRAME,
            })
    outputs = list(refs.values())
    if source["artifact_id"] not in refs:
        outputs.insert(0, source)
    return validate_result(result_envelope(request, observations, outputs, "partial", warnings), request)
