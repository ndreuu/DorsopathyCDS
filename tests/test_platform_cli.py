from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from dorsopathy_platform.cli import main, run_demo
from dorsopathy_platform.contracts import validate_request, validate_result
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository


class PlatformCLITests(unittest.TestCase):
    def test_demo_persists_inspectable_synthetic_results_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = run_demo(root)
            self.assertEqual(len(snapshot["results"]), 2)
            repository = SqliteRepository(root)
            artifacts = ArtifactStore(root)
            self.assertEqual(repository.get_snapshot(snapshot["snapshot_id"]), snapshot)
            for result in snapshot["results"]:
                validate_result(result)
                self.assertEqual(repository.get_job(result["job_id"])["status"], "succeeded")
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["observations"][0]["origin"], "synthetic_demo")
                self.assertTrue(json.loads(artifacts.read(result["artifacts"][0]))["synthetic"])

    def test_reference_wire_documents_validate(self):
        examples = Path(__file__).resolve().parents[1] / "examples/platform"
        request = validate_request(json.loads((examples / "module_request.json").read_text()))
        result = validate_result(json.loads((examples / "module_result.json").read_text()), request)
        self.assertEqual(result["observations"][0]["origin"], "synthetic_demo")

    def test_export_contains_both_versioned_schemas(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["contracts", "--output", directory]), 0)
            for name in ("module_request", "module_result"):
                schema = json.loads((Path(directory) / (name + ".schema.json")).read_text())
                self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")

    def test_cli_deeply_nested_input_returns_safe_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "deep.json"
            request.write_text("[" * 10000 + "null" + "]" * 10000)
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["validate-result", "--request", str(request), "--result", str(request)])
            self.assertEqual(code, 2)
            self.assertNotIn("Traceback", error.getvalue())


if __name__ == "__main__":
    unittest.main()
