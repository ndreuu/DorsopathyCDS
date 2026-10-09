"""Additive migration preserves legacy immutable records and rolls back errors."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dorsopathy_platform import storage
from dorsopathy_platform.storage import SqliteRepository, StorageError
from test_platform_storage import request, result_for


class StorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repo = SqliteRepository(self.root)
        repo.create_episode("episode-one")
        payload = request()
        repo.create_job(payload)
        repo.start_job(payload["job_id"])
        self.result = result_for(payload)
        repo.complete_job(self.result)
        self.snapshot = repo.create_snapshot("episode-one")
        self.job = repo.get_job(payload["job_id"])
        with sqlite3.connect(repo.path) as connection:
            connection.executescript("DROP TABLE workflow_runs; DROP TABLE artifact_catalog; DROP INDEX jobs_status_idx; PRAGMA user_version=0;")
            self.original = {table: connection.execute("SELECT * FROM " + table).fetchall() for table in ("episodes", "jobs", "results", "snapshots")}

    def test_legacy_database_reopens_without_rewriting_records_and_migration_is_idempotent(self):
        for _ in range(2):
            repo = SqliteRepository(self.root)
            self.assertEqual(repo.get_job("job-one"), self.job)
            self.assertEqual(repo.get_result("result-one"), self.result)
            self.assertEqual(repo.get_snapshot(self.snapshot["snapshot_id"]), self.snapshot)
            self.assertEqual(repo.list_artifacts("episode-one"), [])
            with sqlite3.connect(repo.path) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual({table: connection.execute("SELECT * FROM " + table).fetchall() for table in self.original}, self.original)

    def test_failure_rolls_back_new_schema_and_keeps_legacy_rows(self):
        with patch.object(storage, "_MIGRATION_DDL", storage._MIGRATION_DDL + " INVALID SQL STATEMENT;"):
            with self.assertRaises(StorageError):
                SqliteRepository(self.root)
        with sqlite3.connect(self.root / "platform.sqlite3") as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("artifact_catalog", names)
            self.assertNotIn("workflow_runs", names)
            self.assertEqual({table: connection.execute("SELECT * FROM " + table).fetchall() for table in self.original}, self.original)
        self.assertEqual(SqliteRepository(self.root).get_result("result-one"), self.result)

    def test_future_version_is_rejected_without_resetting_database(self):
        with sqlite3.connect(self.root / "platform.sqlite3") as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(StorageError):
            SqliteRepository(self.root)
        with sqlite3.connect(self.root / "platform.sqlite3") as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual({table: connection.execute("SELECT * FROM " + table).fetchall() for table in self.original}, self.original)


if __name__ == "__main__":
    unittest.main()
