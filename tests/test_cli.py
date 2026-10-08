from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from dorsopathy_cds.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_evaluate_writes_a_traceable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "nested/report.json"
            with redirect_stdout(io.StringIO()):
                code = main(["evaluate", str(ROOT / "examples/01_supported_with_imaging.json"), "--output", str(report)])
            self.assertEqual(code, 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["results"][0]["status"], "met")
            self.assertEqual(len(payload["results"][0]["citations"]), 3)

    def test_duplicate_facts_and_nonfinite_json_are_rejected_without_echo(self):
        bodies = (
            '{"population":"adult","facts":{},"facts":{},"case_id":"PRIVATE_FIXTURE"}',
            '{"population":"adult","facts":{},"case_id":NaN}',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            for body in bodies:
                with self.subTest(body=body):
                    path.write_text(body, encoding="utf-8")
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        self.assertEqual(main(["evaluate", str(path)]), 2)
                    self.assertNotIn("PRIVATE_FIXTURE", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
