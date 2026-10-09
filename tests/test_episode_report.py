"""Frozen report determinism, saved quotes and escaped portable HTML."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dorsopathy_cds.episode_report import evaluate_snapshot, render_html, ReportError
from dorsopathy_cds.service import load_catalog
from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_platform.storage import ArtifactStore
from test_snapshot_adapter import make_snapshot, make_manifest, make_mri, make_clinical


class EpisodeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))
        self.mri = make_mri(self.store)
        self.exam = make_clinical(self.store, self.mri)
        self.snapshot = make_snapshot([self.mri, self.exam])
        self.manifest = make_manifest([self.mri, self.exam])

    def test_report_hash_is_deterministic_and_frozen_inputs_not_mutated(self):
        original = deepcopy((self.snapshot, self.manifest))
        first = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        second = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        self.assertEqual(first, second)
        self.assertEqual((self.snapshot, self.manifest), original)
        content = {key: value for key, value in first.items() if key not in {"report_id", "content_sha256"}}
        self.assertEqual(hashlib.sha256(canonical_bytes(content)).hexdigest(), first["content_sha256"])
        self.assertEqual(first["report_id"], "report-" + first["content_sha256"])
        self.assertNotIn("generated_at", first)

    def test_saved_json_and_html_ignore_later_catalogue_or_selection_changes(self):
        report = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        html = render_html(report)
        rules, sources = load_catalog()
        sources["documents"][0]["quotes"][0]["exact_quote"] += " revised"
        with patch("dorsopathy_cds.service.load_catalog", return_value=(rules, sources)), patch("dorsopathy_cds.episode_report.load_catalog", return_value=(rules, sources)):
            self.assertEqual(render_html(report), html)
            changed = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        self.assertNotEqual(changed["report_id"], report["report_id"])
        self.manifest["clinical_selection"]["imaging.study_status"] = "missing-observation"
        self.assertEqual(render_html(report), html)
        changed_selection = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        self.assertNotEqual(changed_selection["report_id"], report["report_id"])

    def test_literal_quotes_sources_and_lineage_are_exposed_without_disease_exclusion(self):
        report = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        html = render_html(report)
        self.assertIn("Гипотеза поддерживается", html)
        for citation in report["clinical_report"]["results"][0]["citations"]:
            self.assertIn(citation["exact_quote"], html)
        self.assertIn("apicr.minzdrav.gov.ru", html)
        self.assertIn("source_artifact_id", html)
        self.assertIn("technical_comparison_only", html)

    def test_html_escapes_all_saved_user_metadata_and_checks_report_integrity(self):
        report = evaluate_snapshot(self.snapshot, self.manifest, self.store)
        report["warnings"].append({"code": "example", "message": '<img src=x onerror="alert(1)">'})
        content = {key: value for key, value in report.items() if key not in {"report_id", "content_sha256"}}
        report["content_sha256"] = hashlib.sha256(canonical_bytes(content)).hexdigest()
        report["report_id"] = "report-" + report["content_sha256"]
        html = render_html(report)
        self.assertNotIn('<img src=x', html)
        self.assertIn("&lt;img", html)
        report["case"]["population"] = "changed"
        with self.assertRaises(ReportError):
            render_html(report)

    def test_wrong_comparison_snapshot_and_catalog_race_are_rejected(self):
        with self.assertRaises(ReportError):
            evaluate_snapshot(self.snapshot, self.manifest, self.store, comparison={"schema_version": "1.0", "snapshot_id": "wrong"})
        rules, sources = load_catalog()
        sources["documents"][0]["quotes"][0]["exact_quote"] += " changed"
        with patch("dorsopathy_cds.episode_report.load_catalog", return_value=(rules, sources)), self.assertRaises(ReportError):
            evaluate_snapshot(self.snapshot, self.manifest, self.store)


if __name__ == "__main__":
    unittest.main()
