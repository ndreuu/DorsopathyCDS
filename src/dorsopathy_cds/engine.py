"""Deterministic, source-linked rules with explicit three-valued evaluation.

The engine checks a rule against a local quotation allowlist; it does not verify
that a document is clinically applicable, current, or regulator-approved.  A
result reports whether supplied facts satisfy a rule.  It is not a diagnosis.

Rules use ``condition`` trees with ``all`` / ``any`` branches and ``eq`` leaves.
Each leaf has an ``id``, ``fact``, ``expected`` scalar and ``citations``.  A
citation references ``doc_id`` and ``quote_id``.  The source registry has a
``documents`` list; each document contains ``id`` and a ``quotes`` list.  Quotes
contain ``id``, ``exact_quote``, a one-based PDF ``page`` and ``locator``.  Quote
contents are resolved once in the report; every leaf retains its source links.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any


class DefinitionError(ValueError):
    """A rule or source registry cannot be evaluated safely."""


class InputValidationError(ValueError):
    """A supplied fact has an incompatible or unsupported type."""


_CITATION_FIELDS = ("doc_id", "quote_id", "exact_quote", "page", "locator")
_STATUSES = {"true": "met", "false": "not_met", "unknown": "indeterminate"}


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DefinitionError(f"{label} must be a non-empty string")
    return value


def _scalar_family(value: Any) -> str | None:
    # bool is a subclass of int in Python.  Check it first to prevent coercion.
    if type(value) is bool:
        return "boolean"
    if type(value) is str:
        return "string"
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            return None
        return "number"
    return None


def _source_index(sources: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    if not isinstance(sources, Mapping):
        raise DefinitionError("sources must be a mapping")
    documents = sources.get("documents")
    if not isinstance(documents, list):
        raise DefinitionError("sources.documents must be a list")

    index: dict[tuple[str, str], dict[str, Any]] = {}
    document_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, Mapping):
            raise DefinitionError("each source document must be a mapping")
        doc_id = _nonempty_string(document.get("id"), "source document id")
        if doc_id in document_ids:
            raise DefinitionError(f"duplicate source document id: {doc_id}")
        document_ids.add(doc_id)
        quotes = document.get("quotes")
        if not isinstance(quotes, list):
            raise DefinitionError(f"source {doc_id}.quotes must be a list")
        for quote in quotes:
            if not isinstance(quote, Mapping):
                raise DefinitionError(f"source {doc_id} quotation must be a mapping")
            quote_id = _nonempty_string(quote.get("id"), "quotation id")
            exact_quote = _nonempty_string(quote.get("exact_quote"), "exact_quote")
            locator = _nonempty_string(quote.get("locator"), "quotation locator")
            page = quote.get("page")
            if type(page) is not int or page < 1:
                raise DefinitionError("quotation page must be a positive one-based integer")
            key = (doc_id, quote_id)
            if key in index:
                raise DefinitionError(f"duplicate quotation id in {doc_id}: {quote_id}")
            index[key] = {
                "doc_id": doc_id,
                "quote_id": quote_id,
                "exact_quote": exact_quote,
                "page": page,
                "locator": locator,
            }
    return index


def _validate_citations(
    citations: Any, index: Mapping[tuple[str, str], dict[str, Any]], label: str
) -> None:
    if not isinstance(citations, list) or not citations:
        raise DefinitionError(f"{label}.citations must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        if not isinstance(citation, Mapping):
            raise DefinitionError(f"{label}: each citation must be a mapping")
        doc_id = _nonempty_string(citation.get("doc_id"), "citation doc_id")
        quote_id = _nonempty_string(citation.get("quote_id"), "citation quote_id")
        key = (doc_id, quote_id)
        if key not in index:
            raise DefinitionError(f"{label}: undeclared source quotation {doc_id}/{quote_id}")
        if key in seen:
            raise DefinitionError(f"{label}: duplicate citation {doc_id}/{quote_id}")
        seen.add(key)
        # References are the canonical form.  Legacy inline quote fields are
        # allowed only if they match the registry exactly, never as overrides.
        for field in _CITATION_FIELDS:
            if field in citation:
                value = citation[field]
                expected = index[key][field]
                if type(value) is not type(expected) or value != expected:
                    raise DefinitionError(f"{label}: citation {field} differs from source quotation")


def _validate_node(
    node: Any,
    index: Mapping[tuple[str, str], dict[str, Any]],
    leaf_ids: set[str],
    fact_families: dict[str, str],
) -> None:
    if not isinstance(node, Mapping):
        raise DefinitionError("each condition node must be a mapping")
    branches = [operator for operator in ("all", "any") if operator in node]
    allowed_fields = (
        {"id", "all", "any", "label", "description"}
        if branches else {"id", "fact", "operator", "expected", "citations", "label", "description"}
    )
    unsupported_fields = set(node) - allowed_fields
    if unsupported_fields:
        raise DefinitionError(f"unsupported condition fields: {sorted(unsupported_fields)}")
    if branches:
        if len(branches) != 1 or any(
            field in node for field in ("fact", "operator", "expected", "citations")
        ):
            raise DefinitionError("a condition must be either one all/any branch or a leaf")
        operator = branches[0]
        children = node[operator]
        if not isinstance(children, list) or not children:
            raise DefinitionError(f"{operator} must contain a non-empty list of conditions")
        for child in children:
            _validate_node(child, index, leaf_ids, fact_families)
        return

    leaf_id = _nonempty_string(node.get("id"), "leaf id")
    if leaf_id in leaf_ids:
        raise DefinitionError(f"duplicate leaf id: {leaf_id}")
    leaf_ids.add(leaf_id)
    fact_id = _nonempty_string(node.get("fact"), f"{leaf_id}.fact")
    if node.get("operator") != "eq":
        raise DefinitionError(f"{leaf_id}: only the eq operator is supported")
    family = _scalar_family(node.get("expected"))
    if family is None:
        raise DefinitionError(f"{leaf_id}.expected must be a boolean, string or finite number")
    prior_family = fact_families.setdefault(fact_id, family)
    if prior_family != family:
        raise DefinitionError(f"fact {fact_id} has contradictory expected types")
    _validate_citations(node.get("citations"), index, leaf_id)


def validate_rules(rules: Sequence[Mapping[str, Any]], sources: Mapping[str, Any]) -> None:
    """Reject malformed definitions and citations outside the source allowlist.

    Validation never repairs quotations, trims their contents or infers missing
    citations.  If a rule repeats quote text, a one-character difference from
    the source quotation causes an error.
    """
    if not isinstance(rules, (list, tuple)) or not rules:
        raise DefinitionError("rules must be a non-empty list or tuple")
    index = _source_index(sources)
    rule_ids: set[str] = set()
    fact_families: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise DefinitionError("each rule must be a mapping")
        rule_id = _nonempty_string(rule.get("id"), "rule id")
        if rule_id in rule_ids:
            raise DefinitionError(f"duplicate rule id: {rule_id}")
        rule_ids.add(rule_id)
        _nonempty_string(rule.get("version"), f"{rule_id}.version")
        _validate_node(rule.get("condition"), index, set(), fact_families)


def _combine(operator: str, child_values: list[str]) -> str:
    if operator == "all":
        if "false" in child_values:
            return "false"
        return "unknown" if "unknown" in child_values else "true"
    if "true" in child_values:
        return "true"
    return "unknown" if "unknown" in child_values else "false"


def _evaluate_node(
    node: Mapping[str, Any], facts: Mapping[str, Any], leaves: list[dict[str, Any]]
) -> dict[str, Any]:
    for operator in ("all", "any"):
        if operator in node:
            children = [_evaluate_node(child, facts, leaves) for child in node[operator]]
            value = _combine(operator, [child["value"] for child in children])
            return {
                "operator": operator,
                "value": value,
                "decisive_missing_fact_ids": sorted(
                    {
                        fact_id
                        for child in children
                        for fact_id in child["decisive_missing_fact_ids"]
                    }
                ) if value == "unknown" else [],
                "children": children,
            }

    fact_id = node["fact"]
    present = fact_id in facts
    observed = facts.get(fact_id)
    unknown_reason = None
    if not present:
        value = "unknown"
        unknown_reason = "missing"
    elif observed is None:
        value = "unknown"
        unknown_reason = "null"
    else:
        family = _scalar_family(node["expected"])
        if _scalar_family(observed) != family:
            raise InputValidationError(f"fact {fact_id} must be {family} or null")
        value = "true" if observed == node["expected"] else "false"

    trace = {
        "leaf_id": node["id"],
        "fact_id": fact_id,
        "operator": "eq",
        "expected": deepcopy(node["expected"]),
        "present": present,
        "observed": deepcopy(observed),
        "value": value,
        "unknown_reason": unknown_reason,
        "decisive_missing_fact_ids": [fact_id] if value == "unknown" else [],
        "citation_refs": [
            {"doc_id": citation["doc_id"], "quote_id": citation["quote_id"]}
            for citation in node["citations"]
        ],
    }
    leaves.append(trace)
    return trace


def evaluate(
    rule: Mapping[str, Any], facts: Mapping[str, Any], sources: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a complete, reproducible evaluation without short-circuiting.

    Missing and explicit null facts are unknown; false, zero and empty strings
    remain actual supplied values.  All supplied values for referenced facts
    must have the expected type.  Unknown facts are reported even when another
    child makes the overall outcome definite.
    """
    validate_rules([rule], sources)
    index = _source_index(sources)
    if not isinstance(facts, Mapping) or any(type(key) is not str for key in facts):
        raise InputValidationError("facts must be a mapping with string keys")

    leaves: list[dict[str, Any]] = []
    trace = _evaluate_node(rule["condition"], facts, leaves)
    citations: list[dict[str, Any]] = []
    seen_citations: set[tuple[str, str]] = set()
    for leaf in leaves:
        for citation in leaf["citation_refs"]:
            key = (citation["doc_id"], citation["quote_id"])
            if key not in seen_citations:
                seen_citations.add(key)
                citations.append(deepcopy(index[key]))

    fact_ids = sorted({leaf["fact_id"] for leaf in leaves})
    return {
        "rule_id": rule["id"],
        "rule_version": rule["version"],
        "status": _STATUSES[trace["value"]],
        "value": trace["value"],
        "fact_ids": fact_ids,
        "missing_fact_ids": sorted(
            {leaf["fact_id"] for leaf in leaves if leaf["value"] == "unknown"}
        ),
        "decisive_missing_fact_ids": trace["decisive_missing_fact_ids"],
        "trace": trace,
        "leaves": leaves,
        "citations": citations,
        "input_facts": {fact_id: deepcopy(facts[fact_id]) for fact_id in fact_ids if fact_id in facts},
    }
