"""Validated case boundary and clinical interpretation of rule-engine results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import __version__
from .engine import evaluate, validate_rules

CATALOG_DIR = Path(__file__).parent / "catalog"
FACT_SCHEMA = {
    "clinical.specific_cause_warning_signs_absent": bool,
    "clinical.radiculopathy_signs_and_positive_tension_tests": bool,
    "clinical.imaging_clinical_concordance": bool,
    "imaging.study_status": str,
}
STUDY_STATUSES = {"not_performed", "performed_result_available"}
ORIGINS = {"clinician_entered", "synthetic_demo"}


class PayloadError(ValueError):
    """Invalid case envelope; messages never include supplied patient values."""


def load_catalog() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rules = json.loads((CATALOG_DIR / "rules.json").read_text(encoding="utf-8"))
    sources = json.loads((CATALOG_DIR / "sources.json").read_text(encoding="utf-8"))
    validate_rules(rules, sources)
    return rules, sources


def _validate_case(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(payload) is not dict or set(payload) - {"case_id", "population", "facts"}:
        raise PayloadError("Expected a case object with case_id, population and facts only")
    if payload.get("population") != "adult":
        raise PayloadError("This research profile requires an explicitly declared adult population")
    case_id = payload.get("case_id")
    if case_id is not None and (type(case_id) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", case_id) is None):
        raise PayloadError("Invalid case_id")
    facts = payload.get("facts")
    if type(facts) is not dict or set(facts) - set(FACT_SCHEMA):
        raise PayloadError("Unknown fact or invalid facts object")
    values: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for fact_id, entry in facts.items():
        if type(entry) is not dict or set(entry) - {"value", "origin", "assessment_ref"} or "value" not in entry:
            raise PayloadError("Each fact requires a value and supported provenance fields")
        value = entry["value"]
        if value is not None and type(value) is not FACT_SCHEMA[fact_id]:
            raise PayloadError("Fact value has incorrect type")
        if fact_id == "imaging.study_status" and value is not None and value not in STUDY_STATUSES:
            raise PayloadError("Unsupported imaging study status")
        origin = entry.get("origin")
        assessment_ref = entry.get("assessment_ref")
        if origin is not None and (type(origin) is not str or origin not in ORIGINS):
            raise PayloadError("Unsupported fact origin")
        if assessment_ref is not None and (type(assessment_ref) is not str or not assessment_ref.strip() or len(assessment_ref) > 120):
            raise PayloadError("Invalid assessment reference")
        if value is not None and (origin is None or assessment_ref is None):
            raise PayloadError("Known facts require clinician or demo origin and assessment reference")
        values[fact_id] = value
        provenance[fact_id] = {"origin": origin, "assessment_ref": assessment_ref}
    if values.get("imaging.study_status") == "not_performed" and values.get("clinical.imaging_clinical_concordance") is not None:
        raise PayloadError("Imaging concordance is not applicable when the study was not performed")
    if values.get("clinical.imaging_clinical_concordance") is not None and values.get("imaging.study_status") != "performed_result_available":
        raise PayloadError("Known imaging concordance requires an available study result")
    return values, provenance


def evaluate_case(payload: dict[str, Any]) -> dict[str, Any]:
    values, provenance = _validate_case(payload)
    rules, sources = load_catalog()
    results = [evaluate(rule, values, sources) for rule in rules]
    for result in results:
        result["clinical_review_status"] = "requires_clinical_review"
        result["interpretation"] = {
            "met": "Условия формализованного правила выполнены: гипотеза поддерживается и требует проверки врачом.",
            "not_met": "Условия формализованного правила не выполнены. Заболевание этим результатом не исключается.",
            "indeterminate": "Для проверки условий правила недостаточно данных.",
        }[result["status"]]
        result["imaging_concordance_status"] = (
            "not_applicable" if values.get("imaging.study_status") == "not_performed"
            else "not_assessed" if values.get("clinical.imaging_clinical_concordance") is None
            else "concordant" if values["clinical.imaging_clinical_concordance"] else "discordant"
        )
        result["next_action"] = (
            "complete_missing_clinical_assessments_then_review"
            if result["decisive_missing_fact_ids"] else "clinician_review"
        )
    canonical_catalog = json.dumps({"rules": rules, "sources": sources}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": "1.0",
        "software_version": __version__,
        "mode": "research_only",
        "clinical_review_status": "requires_clinical_review",
        "synthetic_demo": any(item["origin"] == "synthetic_demo" for item in provenance.values()),
        "case_id": payload.get("case_id"),
        "population": "adult",
        "clinical_profile": "RU-research",
        "formalization_status": "draft_pending_clinical_validation",
        "catalog_sha256": hashlib.sha256(canonical_catalog.encode("utf-8")).hexdigest(),
        "source_documents": [
            {key: value for key, value in document.items() if key != "quotes"}
            for document in sources["documents"]
        ],
        "facts_provenance": provenance,
        "results": results,
    }
