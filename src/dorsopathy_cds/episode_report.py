"""Deterministic clinical/comparison exports with a frozen source catalogue."""

from copy import deepcopy
import hashlib
from html import escape
import json
from urllib.parse import urlsplit

from dorsopathy_platform.api_contracts import canonical_bytes, validate_manifest
from dorsopathy_platform.comparison import compare_snapshot

from .service import evaluate_case, load_catalog
from .snapshot_adapter import ADAPTER_VERSION, build_case

_FIELDS = {"schema_version", "snapshot_id", "adapter_version", "manifest", "selection",
           "catalog_snapshot", "catalog_sha256", "case", "clinical_report", "lineage", "warnings", "comparison",
           "report_id", "content_sha256"}


class ReportError(ValueError):
    """Invalid frozen report; errors omit patient values."""


def evaluate_snapshot(snapshot, manifest, artifacts, comparison=None):
    case, lineage, warnings = build_case(snapshot, manifest, artifacts)
    manifest = validate_manifest(manifest)
    rules, sources = load_catalog()
    catalog = {"rules": rules, "sources": sources}
    catalog_sha = hashlib.sha256(canonical_bytes(catalog)).hexdigest()
    clinical = evaluate_case(case)
    if clinical["catalog_sha256"] != catalog_sha:
        raise ReportError("Clinical source catalogue changed during evaluation")
    comparison = compare_snapshot(snapshot, manifest, artifacts) if comparison is None else deepcopy(comparison)
    if type(comparison) is not dict or comparison.get("schema_version") != "1.0" or comparison.get("snapshot_id") != snapshot["snapshot_id"]:
        raise ReportError("Comparison does not match the frozen snapshot")
    content = {
        "schema_version": "1.0", "snapshot_id": snapshot["snapshot_id"], "adapter_version": ADAPTER_VERSION,
        "manifest": manifest, "selection": deepcopy(manifest["clinical_selection"]),
        "catalog_snapshot": catalog, "catalog_sha256": catalog_sha, "case": case,
        "clinical_report": clinical, "lineage": lineage, "warnings": warnings, "comparison": comparison,
    }
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    return deepcopy(dict(content, report_id="report-" + sha, content_sha256=sha))


def _validate_report(report):
    if type(report) is not dict or set(report) != _FIELDS or report["schema_version"] != "1.0":
        raise ReportError("Unsupported frozen report envelope")
    content = {key: value for key, value in report.items() if key not in {"report_id", "content_sha256"}}
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    if report["content_sha256"] != sha or report["report_id"] != "report-" + sha:
        raise ReportError("Frozen report integrity check failed")
    if hashlib.sha256(canonical_bytes(report["catalog_snapshot"])).hexdigest() != report["catalog_sha256"] or report["clinical_report"]["catalog_sha256"] != report["catalog_sha256"]:
        raise ReportError("Frozen report catalogue integrity check failed")
    return report


def _json(value):
    return escape(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def render_html(report):
    """Render only saved content; never consult the current rules/catalogue."""
    report = _validate_report(report)
    statuses = {"met": "Гипотеза поддерживается", "not_met": "Условия не выполнены",
                "indeterminate": "Недостаточно данных"}
    sections = []
    for result in report["clinical_report"]["results"]:
        rows = []
        for leaf in result["leaves"]:
            rows.append("<tr><td>" + escape(leaf["fact_id"]) + "</td><td>" + escape(str(leaf["observed"])) + "</td><td>" + escape(leaf["value"]) + "</td></tr>")
        quotes = "".join("<li><blockquote>" + escape(item["exact_quote"]) + "</blockquote><small>" + escape(str(item["doc_id"])) + ", с. " + escape(str(item["page"])) + "; " + escape(str(item["locator"])) + "</small></li>" for item in result["citations"])
        sections.append("<section><h2>" + escape(statuses[result["status"]]) + "</h2><p>" + escape(result["interpretation"]) + "</p><table><thead><tr><th>Условие</th><th>Значение</th><th>Проверка</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table><h3>Прямые цитаты</h3><ul>" + quotes + "</ul></section>")
    sources = []
    for document in report["catalog_snapshot"]["sources"]["documents"]:
        title = escape(document["title"])
        url = document.get("pdf_url", "")
        if type(url) is str and urlsplit(url).scheme in {"https", "http"}:
            title = '<a href="' + escape(url, quote=True) + '" rel="noopener noreferrer">' + title + "</a>"
        sources.append("<li>" + title + "; версия " + escape(str(document["guideline_version"])) + "; " + escape(str(document["approval_year"])) + "</li>")
    details = "".join("<details><summary>" + title + "</summary><pre>" + _json(report[key]) + "</pre></details>" for title, key in (("Проверяемое происхождение условий", "lineage"), ("Техническое сопоставление модулей", "comparison"), ("Неизвестные данные и ограничения", "warnings"), ("Замороженный отчёт JSON", None)) if key is not None)
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Отчёт DorsopathyCDS</title><style>body{font:16px system-ui;max-width:1050px;margin:32px auto;padding:0 20px;line-height:1.5}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:16px}blockquote{border-left:3px solid #777;margin:12px 0;padding-left:12px}small{color:#555}details{margin:16px 0}section{margin:32px 0}</style><body><h1>Проверка клинической гипотезы</h1><p>Исследовательский модуль. Требуется клиническая проверка врачом.</p><p>Snapshot: <code>' + escape(report["snapshot_id"]) + '</code></p>' + "".join(sections) + "<h3>Источники сохранённой редакции</h3><ul>" + "".join(sources) + "</ul>" + details + '<details><summary>Замороженный отчёт JSON</summary><pre>' + _json(report) + "</pre></details></body></html>"
