"""HTTP protocol boundaries and absence of sensitive exception disclosure."""

from contextlib import redirect_stderr
import http.client as http_client
from io import StringIO
import json
import threading
import unittest
from unittest.mock import patch

from dorsopathy_cds import http, service
from dorsopathy_cds.engine import DefinitionError, InputValidationError


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = http.create_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None, headers=None):
        connection = http_client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            response_headers = dict(response.getheaders())
            self.assertEqual(int(response_headers["Content-Length"]), len(data))
            self.assertEqual(response_headers["Cache-Control"], "no-store")
            self.assertTrue(response_headers["Content-Type"].startswith("application/json"))
            return response.status, json.loads(data), data
        finally:
            connection.close()

    def raw_headers_request(self, headers):
        connection = http_client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.putrequest("POST", "/evaluate")
            for key, value in headers:
                connection.putheader(key, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_health_is_research_only(self):
        status, payload, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "mode": "research_only"})

    def test_rules_preserve_exact_quotes_and_canonical_references(self):
        rules = [{"id": "fixture", "condition": {"citations": [{"doc_id": "d1", "quote_id": "q1"}]}}]
        quote = "Точная цитата — без изменений."
        sources = {"documents": [{"id": "d1", "quotes": [{"id": "q1", "exact_quote": quote}]}]}
        with patch.object(service, "load_catalog", return_value=(rules, sources)):
            status, payload, wire = self.request("GET", "/rules")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"rules": rules, "sources": sources})
        self.assertIn(quote.encode("utf-8"), wire)

    def test_evaluate_transmits_fact_provenance_and_report(self):
        case = {"case_id": "synthetic-http", "facts": {"fixture": {"value": True, "origin": "clinician_entered", "assessment_ref": "synthetic-exam"}}}
        report = {"case_id": "synthetic-http", "status": "indeterminate", "missing_fact_ids": ["another_fixture"]}
        with patch.object(service, "evaluate_case", return_value=report) as evaluate:
            status, payload, _ = self.request("POST", "/evaluate", json.dumps(case), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(payload, report)
        evaluate.assert_called_once_with(case)

    def test_real_catalog_evaluation_retains_quotes_for_unknown_case(self):
        status, catalog, _ = self.request("GET", "/rules")
        self.assertEqual(status, 200)
        quotations = {
            (document["id"], quote["id"]): quote["exact_quote"]
            for document in catalog["sources"]["documents"]
            for quote in document["quotes"]
        }
        status, report, _ = self.request(
            "POST", "/evaluate",
            json.dumps({"case_id": "synthetic-missing-http", "population": "adult", "facts": {}}),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["mode"], "research_only")
        self.assertTrue(report["results"])
        for result in report["results"]:
            self.assertEqual(result["status"], "indeterminate")
            self.assertTrue(result["decisive_missing_fact_ids"])
            self.assertTrue(result["citations"])
            for citation in result["citations"]:
                key = (citation["doc_id"], citation["quote_id"])
                self.assertEqual(citation["exact_quote"], quotations[key])

    def test_unknown_routes_return_not_found(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                status, payload, _ = self.request(method, "/missing")
                self.assertEqual(status, 404)
                self.assertEqual(payload["error"]["code"], "not_found")

    def test_non_json_media_type_is_rejected(self):
        status, payload, _ = self.request("POST", "/evaluate", "{}", {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"]["code"], "unsupported_media_type")

    def test_missing_length_is_rejected(self):
        status, payload = self.raw_headers_request([("Content-Type", "application/json")])
        self.assertEqual(status, 411)
        self.assertEqual(payload["error"]["code"], "length_required")

    def test_ambiguous_or_invalid_lengths_are_rejected(self):
        for lengths in (["-1"], ["+1"], ["0", "0"], ["1, 1"]):
            with self.subTest(lengths=lengths):
                headers = [("Content-Type", "application/json")] + [("Content-Length", length) for length in lengths]
                status, _ = self.raw_headers_request(headers)
                self.assertEqual(status, 400)

    def test_transfer_encoding_is_not_accepted(self):
        status, _ = self.raw_headers_request([("Content-Type", "application/json"), ("Content-Length", "0"), ("Transfer-Encoding", "chunked")])
        self.assertEqual(status, 400)

    def test_oversized_body_is_rejected_before_evaluation(self):
        with patch.object(service, "evaluate_case") as evaluate:
            status, payload, _ = self.request("POST", "/evaluate", b" " * (http.MAX_BODY_BYTES + 1), {"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "payload_too_large")
        evaluate.assert_not_called()

    def test_invalid_ambiguous_and_nonfinite_json_are_rejected(self):
        for body in (b"{", b"\xff", b'{"value": NaN}', b'{"value": Infinity}', b'{"a": 1, "a": 2}', b"[" * 1200 + b"0" + b"]" * 1200):
            with self.subTest(body=body):
                status, payload, _ = self.request("POST", "/evaluate", body, {"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_json_non_object_is_rejected(self):
        status, payload, _ = self.request("POST", "/evaluate", "[]", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_case")

    def test_input_errors_are_bad_requests_without_exception_details(self):
        for error in (service.PayloadError("sensitive-case-123"), InputValidationError("sensitive-case-123")):
            with self.subTest(error=type(error).__name__), patch.object(service, "evaluate_case", side_effect=error):
                status, payload, wire = self.request("POST", "/evaluate", "{}", {"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_case")
                self.assertNotIn(b"sensitive-case-123", wire)

    def test_server_errors_do_not_disclose_exception_or_log_it(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            for error in (RuntimeError("sensitive-case-123"), DefinitionError("sensitive-case-123")):
                with self.subTest(error=type(error).__name__), patch.object(service, "evaluate_case", side_effect=error):
                    status, payload, wire = self.request("POST", "/evaluate", "{}", {"Content-Type": "application/json"})
                    self.assertEqual(status, 500)
                    self.assertEqual(payload["error"]["code"], "internal_error")
                    self.assertNotIn(b"sensitive-case-123", wire)
        self.assertEqual(stderr.getvalue(), "")

    def test_catalog_errors_are_generic_server_errors(self):
        with patch.object(service, "load_catalog", side_effect=DefinitionError("private-catalog-detail")):
            status, _, wire = self.request("GET", "/rules")
        self.assertEqual(status, 500)
        self.assertNotIn(b"private-catalog-detail", wire)

    def test_non_loopback_binding_is_rejected(self):
        for host in ("0.0.0.0", "192.168.1.2", "example.org"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                http.create_server(host, 0)


if __name__ == "__main__":
    unittest.main()
