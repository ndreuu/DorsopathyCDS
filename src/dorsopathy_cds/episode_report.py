"""Deterministic clinical/comparison exports with a frozen source catalogue."""

from copy import deepcopy
import hashlib
from html import escape
import json
from urllib.parse import urlsplit

from dorsopathy_platform.api_contracts import canonical_bytes, validate_manifest
from dorsopathy_platform.comparison import compare_snapshot

from .service import evaluate_case, load_catalog
from .module_rules import evaluate_modules
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
        "schema_version": "2.0", "snapshot_id": snapshot["snapshot_id"], "adapter_version": ADAPTER_VERSION,
        "manifest": manifest, "selection": deepcopy(manifest["clinical_selection"]),
        "catalog_snapshot": catalog, "catalog_sha256": catalog_sha, "case": case,
        "clinical_report": clinical, "lineage": lineage, "warnings": warnings, "comparison": comparison,
        "module_report": evaluate_modules(snapshot, manifest, artifacts),
    }
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    return deepcopy(dict(content, report_id="report-" + sha, content_sha256=sha))


def _validate_report(report):
    if type(report) is not dict:
        raise ReportError("Unsupported frozen report envelope")
    version = report.get("schema_version")
    fields = _FIELDS if version == '1.0' else _FIELDS | {'module_report'}
    if version not in {'1.0', '2.0'} or set(report) != fields:
        raise ReportError("Unsupported frozen report envelope")
    content = {key: value for key, value in report.items() if key not in {"report_id", "content_sha256"}}
    sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    if report["content_sha256"] != sha or report["report_id"] != "report-" + sha:
        raise ReportError("Frozen report integrity check failed")
    if hashlib.sha256(canonical_bytes(report["catalog_snapshot"])).hexdigest() != report["catalog_sha256"] or report["clinical_report"]["catalog_sha256"] != report["catalog_sha256"]:
        raise ReportError("Frozen report catalogue integrity check failed")
    if version == '2.0':
        module = report['module_report']
        if module['snapshot_id'] != report['snapshot_id'] or hashlib.sha256(canonical_bytes(module['catalog_snapshot'])).hexdigest() != module['catalog_sha256']:
            raise ReportError('Frozen module catalogue integrity check failed')
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
    module = report.get('module_report', {})
    for result in module.get('results', []):
        rows = ''.join('<tr><td>' + escape(leaf['fact_id']) + '</td><td>' + escape(str(leaf['observed'])) + '</td><td>' + escape(leaf['value']) + '</td></tr>' for leaf in result['leaves'])
        quotes = ''.join('<li><blockquote>' + escape(item['exact_quote']) + '</blockquote><small>' + escape(item['doc_id']) + ', с. ' + escape(str(item['page'])) + '; ' + escape(item['locator']) + '</small></li>' for item in result['citations'] + result['action_citations'])
        sections.append('<section><h2>' + escape(result['title']) + '</h2><p>' + escape(result['interpretation']) + '</p><table><tr><th>Ответ пациента</th><th>Значение</th><th>Проверка</th></tr>' + rows + '</table><h3>Цитаты условий и действия</h3><ul>' + quotes + '</ul></section>')
    if module:
        rows=''.join('<tr><td>' + escape(use['module_id'] + ' ' + use['module_version']) + '</td><td>' + escape(use['reason']) + '</td><td>' + str(use['assessed_count']) + ' / ' + str(use['unknown_count']) + '</td></tr>' for use in module['module_use'])
        sections.append('<section><h2>Применение фактов модулей</h2><table><tr><th>Модуль</th><th>Клиническая роль</th><th>Измерено / неизвестно</th></tr>' + rows + '</table><details><summary>Исходные факты, доказательства и привязка к правилам</summary><pre>' + _json(module) + '</pre></details></section>')
    sources = []
    documents = report["catalog_snapshot"]["sources"]["documents"] + module.get('catalog_snapshot', {}).get('sources', {}).get('documents', [])
    for document in documents:
        title = escape(document["title"])
        url = document.get("pdf_url", "")
        if type(url) is str and urlsplit(url).scheme in {"https", "http"}:
            title = '<a href="' + escape(url, quote=True) + '" rel="noopener noreferrer">' + title + "</a>"
        sources.append("<li>" + title + "; версия " + escape(str(document["guideline_version"])) + "; " + escape(str(document.get("approval_year") or "год одобрения не указан")) + "</li>")
    details = "".join("<details><summary>" + title + "</summary><pre>" + _json(report[key]) + "</pre></details>" for title, key in (("Проверяемое происхождение условий", "lineage"), ("Техническое сопоставление модулей", "comparison"), ("Неизвестные данные и ограничения", "warnings"), ("Замороженный отчёт JSON", None)) if key is not None)
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Отчёт DorsopathyCDS</title><style>body{font:16px system-ui;max-width:1050px;margin:32px auto;padding:0 20px;line-height:1.5}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:16px}blockquote{border-left:3px solid #777;margin:12px 0;padding-left:12px}small{color:#555}details{margin:16px 0}section{margin:32px 0}</style><body><h1>Проверка клинической гипотезы</h1><p>Исследовательский модуль. Требуется клиническая проверка врачом.</p><p>Snapshot: <code>' + escape(report["snapshot_id"]) + '</code></p>' + "".join(sections) + "<h3>Источники сохранённой редакции</h3><ul>" + "".join(sources) + "</ul>" + details + '<details><summary>Замороженный отчёт JSON</summary><pre>' + _json(report) + "</pre></details></body></html>"
