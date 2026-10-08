"""Engine verification with synthetic definitions, not clinical guidance."""

from copy import deepcopy
import itertools
import json
import unittest

from dorsopathy_cds.engine import DefinitionError, InputValidationError, evaluate, validate_rules


SOURCE = {
    "documents": [
        {
            "id": "synthetic-test-source",
            "quotes": [
                {
                    "id": "synthetic-q1",
                    "exact_quote": "Synthetic text used only to test source attribution.",
                    "page": 1,
                    "locator": "Synthetic test paragraph",
                }
            ],
        }
    ]
}
CITATION = {
    "doc_id": "synthetic-test-source",
    "quote_id": "synthetic-q1",
    "exact_quote": "Synthetic text used only to test source attribution.",
    "page": 1,
    "locator": "Synthetic test paragraph",
}


def leaf(fact, expected=True, identifier=None):
    return {
        "id": identifier or fact,
        "fact": fact,
        "operator": "eq",
        "expected": expected,
        "citations": [deepcopy(CITATION)],
    }


def rule(condition):
    return {"id": "synthetic-rule", "version": "1.0.0", "condition": condition}


class TruthTableTests(unittest.TestCase):
    def test_all_truth_table_exhaustive(self):
        states = (True, False, None)
        for values in itertools.product(states, repeat=3):
            with self.subTest(values=values):
                facts = dict(zip(("a", "b", "c"), values))
                result = evaluate(rule({"all": [leaf(name) for name in facts]}), facts, SOURCE)
                expected = "not_met" if False in values else "indeterminate" if None in values else "met"
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(result["leaves"]), 3)

    def test_any_truth_table_exhaustive(self):
        states = (True, False, None)
        for values in itertools.product(states, repeat=3):
            with self.subTest(values=values):
                facts = dict(zip(("a", "b", "c"), values))
                result = evaluate(rule({"any": [leaf(name) for name in facts]}), facts, SOURCE)
                expected = "met" if True in values else "indeterminate" if None in values else "not_met"
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(result["leaves"]), 3)

    def test_nested_branches_report_unknown_after_definite_false(self):
        definition = rule({"all": [leaf("a"), {"any": [leaf("b"), leaf("c")]}]})
        result = evaluate(definition, {"a": False, "b": False}, SOURCE)
        self.assertEqual(result["status"], "not_met")
        self.assertEqual(result["missing_fact_ids"], ["c"])
        self.assertEqual(result["decisive_missing_fact_ids"], [])
        self.assertEqual(result["trace"]["children"][1]["value"], "unknown")

    def test_explicit_false_expected(self):
        self.assertEqual(evaluate(rule(leaf("a", False)), {"a": False}, SOURCE)["status"], "met")
        self.assertEqual(evaluate(rule(leaf("a", False)), {"a": True}, SOURCE)["status"], "not_met")

    def test_conditional_requirement_does_not_request_inapplicable_correlation(self):
        definition = rule({"any": [
            leaf("imaging_status", "not_performed", "not-performed"),
            {"all": [
                leaf("imaging_status", "performed", "performed"),
                leaf("correlation"),
            ]},
        ]})
        result = evaluate(definition, {"imaging_status": "not_performed"}, SOURCE)
        self.assertEqual(result["status"], "met")
        self.assertEqual(result["missing_fact_ids"], ["correlation"])
        self.assertEqual(result["decisive_missing_fact_ids"], [])
        result = evaluate(definition, {"imaging_status": "performed"}, SOURCE)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["decisive_missing_fact_ids"], ["correlation"])

    def test_decisive_unknowns_ignore_resolved_nested_branches(self):
        definition = rule({"all": [
            {"any": [leaf("a"), leaf("b")]},
            {"all": [leaf("c"), leaf("d")]},
        ]})
        result = evaluate(definition, {"a": True, "c": True}, SOURCE)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["missing_fact_ids"], ["b", "d"])
        self.assertEqual(result["decisive_missing_fact_ids"], ["d"])


class FactValidationTests(unittest.TestCase):
    def test_missing_and_null_are_unknown_with_distinct_reasons(self):
        definition = rule({"all": [leaf("missing"), leaf("null")]})
        result = evaluate(definition, {"null": None}, SOURCE)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["missing_fact_ids"], ["missing", "null"])
        self.assertEqual(result["decisive_missing_fact_ids"], ["missing", "null"])
        self.assertEqual([item["unknown_reason"] for item in result["leaves"]], ["missing", "null"])
        self.assertEqual(result["input_facts"], {"null": None})

    def test_false_is_not_unknown(self):
        result = evaluate(rule(leaf("a")), {"a": False}, SOURCE)
        self.assertEqual(result["status"], "not_met")
        self.assertEqual(result["missing_fact_ids"], [])

    def test_boolean_does_not_accept_integer_or_truthy_string(self):
        for value in (0, 1, 1.0, "true", "false", [], {}, ()):
            with self.subTest(value=value):
                with self.assertRaises(InputValidationError):
                    evaluate(rule(leaf("a")), {"a": value}, SOURCE)

    def test_number_does_not_accept_boolean(self):
        for value in (True, False, "1", float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(InputValidationError):
                    evaluate(rule(leaf("a", 1)), {"a": value}, SOURCE)

    def test_zero_and_empty_string_are_actual_values(self):
        self.assertEqual(evaluate(rule(leaf("a", 0)), {"a": 0}, SOURCE)["status"], "met")
        self.assertEqual(evaluate(rule(leaf("a", "")), {"a": ""}, SOURCE)["status"], "met")

    def test_numeric_family_accepts_integer_and_float(self):
        self.assertEqual(evaluate(rule(leaf("a", 1)), {"a": 1.0}, SOURCE)["status"], "met")

    def test_invalid_fact_not_hidden_by_a_definite_other_child(self):
        with self.assertRaises(InputValidationError):
            evaluate(rule({"all": [leaf("a"), leaf("b")]}), {"a": False, "b": 1}, SOURCE)

    def test_invalid_facts_mapping(self):
        for facts in ([], {1: True}, None):
            with self.subTest(facts=facts):
                with self.assertRaises(InputValidationError):
                    evaluate(rule(leaf("a")), facts, SOURCE)


class AttributionTests(unittest.TestCase):
    def test_rule_without_citation_is_rejected(self):
        definition = rule(leaf("a"))
        definition["condition"]["citations"] = []
        with self.assertRaises(DefinitionError):
            validate_rules([definition], SOURCE)

    def test_undeclared_source_and_quote_rejected(self):
        for field in ("doc_id", "quote_id"):
            definition = rule(leaf("a"))
            definition["condition"]["citations"][0][field] = "not-declared"
            with self.subTest(field=field):
                with self.assertRaises(DefinitionError):
                    validate_rules([definition], SOURCE)

    def test_exact_quote_and_locator_are_not_normalized(self):
        for field, value in (
            ("exact_quote", CITATION["exact_quote"] + " "),
            ("locator", "Different paragraph"),
            ("page", 2),
            ("page", True),
        ):
            definition = rule(leaf("a"))
            definition["condition"]["citations"][0][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(DefinitionError):
                    validate_rules([definition], SOURCE)

    def test_repeated_citation_is_deduplicated_in_report_only(self):
        result = evaluate(rule({"all": [leaf("a"), leaf("b")]}), {"a": True, "b": True}, SOURCE)
        self.assertEqual(result["citations"], [CITATION])
        self.assertEqual(len(result["leaves"][0]["citation_refs"]), 1)
        self.assertEqual(len(result["leaves"][1]["citation_refs"]), 1)

    def test_reference_only_citations_resolve_quote_contents(self):
        definition = rule(leaf("a"))
        definition["condition"]["citations"] = [
            {"doc_id": CITATION["doc_id"], "quote_id": CITATION["quote_id"]}
        ]
        result = evaluate(definition, {"a": True}, SOURCE)
        self.assertEqual(result["citations"], [CITATION])
        self.assertEqual(result["leaves"][0]["citation_refs"], definition["condition"]["citations"])

    def test_duplicate_source_and_quote_ids_rejected(self):
        source = deepcopy(SOURCE)
        source["documents"].append(deepcopy(source["documents"][0]))
        with self.assertRaises(DefinitionError):
            validate_rules([rule(leaf("a"))], source)
        source = deepcopy(SOURCE)
        source["documents"][0]["quotes"].append(deepcopy(source["documents"][0]["quotes"][0]))
        with self.assertRaises(DefinitionError):
            validate_rules([rule(leaf("a"))], source)


class DefinitionAndReproducibilityTests(unittest.TestCase):
    def test_invalid_definitions_rejected(self):
        conditions = [
            {"all": []},
            {"any": []},
            {"all": [leaf("a")], "any": [leaf("b")]},
            {"all": [leaf("a")], "fact": "a"},
            {"all": [leaf("a"), leaf("b", identifier="a")]},
            {"all": [leaf("a"), leaf("a", expected=1, identifier="other")]},
            leaf("a", expected=None),
            leaf("a", expected=float("nan")),
        ]
        invalid_operator = leaf("a")
        invalid_operator["operator"] = "gt"
        conditions.append(invalid_operator)
        conditions.append({"all": [leaf("a")], "not": True})
        invalid_leaf = leaf("a")
        invalid_leaf["expectd"] = True
        conditions.append(invalid_leaf)
        for condition in conditions:
            with self.subTest(condition=condition):
                with self.assertRaises(DefinitionError):
                    validate_rules([rule(condition)], SOURCE)

    def test_duplicate_rule_ids_rejected(self):
        with self.assertRaises(DefinitionError):
            validate_rules([rule(leaf("a")), rule(leaf("b"))], SOURCE)

    def test_report_is_deterministic_and_inputs_not_mutated(self):
        definition = rule({"all": [leaf("b"), leaf("a")]})
        facts = {"b": True, "a": None, "unused": "not included"}
        snapshots = deepcopy((definition, facts, SOURCE))
        first = evaluate(definition, facts, SOURCE)
        second = evaluate(definition, facts, SOURCE)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual((definition, facts, SOURCE), snapshots)
        self.assertEqual(first["fact_ids"], ["a", "b"])
        first["citations"][0]["exact_quote"] = "changed returned report"
        first["leaves"][0]["citation_refs"][0]["doc_id"] = "changed returned trace"
        self.assertEqual((definition, facts, SOURCE), snapshots)


if __name__ == "__main__":
    unittest.main()
