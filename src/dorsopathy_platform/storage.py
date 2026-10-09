"""Local immutable artifacts, transactional module jobs and frozen snapshots.

The repository stores exact validated JSON, not clinical interpretations.
Errors intentionally omit request bodies, identifiers and filesystem paths.
Each SQLite operation opens its own connection, enabling independent workers.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import BinaryIO, Optional
import uuid

from .contracts import validate_request, validate_result
from .api_contracts import validate_manifest


class StorageError(ValueError):
    """Invalid storage reference, conflict, corruption or missing record."""


class TransitionError(StorageError):
    """A job is not in the state required by the requested transition."""


class ConflictError(StorageError):
    """An immutable identity is already used by different content."""


_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ARTIFACT_FIELDS = {"artifact_id", "storage_key", "sha256", "media_type", "size_bytes", "role"}
_TERMINAL = {"complete": "succeeded", "partial": "partial", "failed": "failed"}
_CHUNK_BYTES = 1024 * 1024
_JOB_STATUSES = frozenset({"queued", "running", "succeeded", "partial", "failed"})
_RUN_TERMINAL = frozenset({"complete", "partial", "failed"})
_DATABASE_VERSION = 1


def _identifier(value) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None or value in {".", ".."}:
        raise StorageError("Invalid opaque record identifier")
    return value


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stored_json(text: str, sha256: str):
    if _digest(text) != sha256:
        raise StorageError("Stored record integrity check failed")
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        raise StorageError("Stored record cannot be decoded") from None


class ArtifactStore:
    """Content-addressed bytes published atomically without overwriting files.

    POSIX directory-relative, no-follow opens prevent traversal through either
    the artifacts directory or an artifact symlink. Configuration root is
    resolved once; references never supply an arbitrary filesystem path.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StorageError("Artifact store cannot be initialized") from None

    @contextmanager
    def _directory(self):
        descriptor = None
        try:
            path = self.root / "artifacts"
            path.mkdir(exist_ok=True)
            descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            yield descriptor
        except OSError:
            raise StorageError("Artifact storage is unavailable or unsafe") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _validate_metadata(media_type: str, role: str) -> None:
        if (
            type(media_type) is not str or not media_type.strip() or len(media_type) > 255
            or not media_type.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in media_type)
        ):
            raise StorageError("Invalid artifact media type")
        if type(role) is not str or role not in {"source", "derived"}:
            raise StorageError("Invalid artifact role")

    @staticmethod
    def _validate_ref(ref: dict) -> dict:
        if type(ref) is not dict or set(ref) != _ARTIFACT_FIELDS:
            raise StorageError("Invalid artifact reference")
        sha = ref["sha256"]
        if type(sha) is not str or _SHA.fullmatch(sha) is None:
            raise StorageError("Invalid artifact digest")
        if ref["artifact_id"] != "artifact-" + sha or ref["storage_key"] != "artifacts/" + sha:
            raise StorageError("Artifact reference must use its canonical relative storage key")
        if type(ref["size_bytes"]) is not int or ref["size_bytes"] < 0:
            raise StorageError("Invalid artifact size")
        ArtifactStore._validate_metadata(ref["media_type"], ref["role"])
        return ref

    @staticmethod
    def _open_at(directory: int, ref: dict) -> BinaryIO:
        descriptor = None
        stream = None
        try:
            descriptor = os.open(ref["sha256"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != ref["size_bytes"]:
                raise StorageError("Artifact size or file type check failed")
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            final_stat = os.fstat(stream.fileno())
            if (
                size != ref["size_bytes"] or digest.hexdigest() != ref["sha256"]
                or final_stat.st_size != file_stat.st_size
                or final_stat.st_mtime_ns != file_stat.st_mtime_ns
            ):
                raise StorageError("Artifact content integrity check failed")
            stream.seek(0)
            verified_stream = stream
            stream = None
            return verified_stream
        except OSError:
            raise StorageError("Artifact is unavailable or unsafe") from None
        finally:
            if stream is not None:
                stream.close()
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _publish_at(directory: int, temporary: str, ref: dict) -> None:
        try:
            # Hard-link publication is atomic and cannot replace an existing
            # name. Concurrent equal uploads verify it without buffering it.
            os.link(temporary, ref["sha256"], src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            with ArtifactStore._open_at(directory, ref):
                pass
        try:
            os.fsync(directory)
        except OSError:
            # Some POSIX filesystems do not permit directory fsync; file data
            # has already been synchronized and atomically published.
            pass

    def add_bytes(self, data: bytes, media_type: str, role: str = "source") -> dict:
        if type(data) is not bytes:
            raise StorageError("Artifact content must be bytes")
        sha = hashlib.sha256(data).hexdigest()
        ref = {
            "artifact_id": "artifact-" + sha,
            "storage_key": "artifacts/" + sha,
            "sha256": sha,
            "media_type": media_type,
            "size_bytes": len(data),
            "role": role,
        }
        self._validate_ref(ref)
        with self._directory() as directory:
            temporary = ".pending-" + uuid.uuid4().hex
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._publish_at(directory, temporary, ref)
            except OSError:
                raise StorageError("Artifact could not be published") from None
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return ref

    def add_file(self, path: Path, media_type: str, role: str = "source") -> dict:
        """Import an explicitly selected provider file using bounded memory.

        The path is a trusted local integration input, not a public HTTP path.
        A regular file is opened without following its final symlink. Changes
        during copying reject publication rather than capture an unstable file.
        """
        self._validate_metadata(media_type, role)
        source_descriptor = None
        try:
            source_descriptor = os.open(str(Path(path)), os.O_RDONLY | os.O_NOFOLLOW)
            initial_stat = os.fstat(source_descriptor)
            if not stat.S_ISREG(initial_stat.st_mode):
                raise StorageError("Artifact source must be a regular file")
            source = os.fdopen(source_descriptor, "rb")
            source_descriptor = None
            with source, self._directory() as directory:
                temporary = ".pending-" + uuid.uuid4().hex
                try:
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    digest = hashlib.sha256()
                    size = 0
                    with os.fdopen(descriptor, "wb") as destination:
                        while True:
                            chunk = source.read(_CHUNK_BYTES)
                            if not chunk:
                                break
                            destination.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                        destination.flush()
                        os.fsync(destination.fileno())
                    final_stat = os.fstat(source.fileno())
                    if size != initial_stat.st_size or final_stat.st_size != initial_stat.st_size or final_stat.st_mtime_ns != initial_stat.st_mtime_ns:
                        raise StorageError("Artifact source changed during import")
                    sha = digest.hexdigest()
                    ref = {
                        "artifact_id": "artifact-" + sha, "storage_key": "artifacts/" + sha,
                        "sha256": sha, "media_type": media_type, "size_bytes": size, "role": role,
                    }
                    self._publish_at(directory, temporary, ref)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
            return ref
        except (OSError, TypeError):
            raise StorageError("Artifact file could not be imported") from None
        finally:
            if source_descriptor is not None:
                os.close(source_descriptor)

    def open(self, ref: dict) -> BinaryIO:
        """Verify content in chunks, rewind, and return a caller-owned handle.

        Use ``with store.open(ref) as stream``. The same no-follow descriptor
        remains open after verification; no path is reopened for the consumer.
        """
        self._validate_ref(ref)
        with self._directory() as directory:
            return self._open_at(directory, ref)

    def read(self, ref: dict) -> bytes:
        with self.open(ref) as stream:
            return stream.read()


_DDL = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    module_id TEXT NOT NULL,
    module_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed')),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    result_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS results (
    result_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    content_sha256 TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
"""

_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS artifact_catalog (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    artifact_id TEXT NOT NULL,
    ref_json TEXT NOT NULL,
    ref_sha256 TEXT NOT NULL,
    PRIMARY KEY (episode_id, artifact_id)
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    idempotency_key TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
    job_ids_json TEXT NOT NULL,
    job_ids_sha256 TEXT NOT NULL,
    snapshot_id TEXT REFERENCES snapshots(snapshot_id),
    report_json TEXT,
    report_sha256 TEXT,
    UNIQUE (episode_id, idempotency_key),
    CHECK ((status = 'running' AND snapshot_id IS NULL AND report_json IS NULL AND report_sha256 IS NULL)
        OR (status != 'running' AND snapshot_id IS NOT NULL AND report_json IS NOT NULL AND report_sha256 IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS workflow_runs_status_idx ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
"""


class SqliteRepository:
    """Short transactions with compare-and-set state changes and JSON lineage."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / "platform.sqlite3"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._connection(write=True) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > _DATABASE_VERSION:
                    raise StorageError("Repository schema version is unsupported")
                # executescript implicitly commits; individual statements keep
                # the entire additive migration inside one write transaction.
                for statement in (_DDL + _MIGRATION_DDL).split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute("PRAGMA user_version=" + str(_DATABASE_VERSION))
        except OSError:
            raise StorageError("Repository cannot be initialized") from None

    @contextmanager
    def _connection(self, write: bool = False):
        connection = None
        try:
            if any((self.root / name).is_symlink() for name in ("platform.sqlite3", "platform.sqlite3-wal", "platform.sqlite3-shm")):
                raise StorageError("Repository storage path is unsafe")
            connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise StorageError("Repository operation failed") from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _episode(connection, episode_id: str) -> dict:
        if connection.execute("SELECT episode_id FROM episodes WHERE episode_id=?", (episode_id,)).fetchone() is None:
            raise StorageError("Episode was not found")
        return {"schema_version": "1.0", "episode_id": episode_id}

    @staticmethod
    def _job_row(connection, job_id: str):
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise StorageError("Job was not found")
        return row

    @staticmethod
    def _job(row) -> dict:
        request = validate_request(_stored_json(row["request_json"], row["request_sha256"]))
        if any(request[field] != row[field] for field in ("job_id", "episode_id", "module_id", "module_version")):
            raise StorageError("Stored job context integrity check failed")
        return {
            "job_id": row["job_id"], "episode_id": row["episode_id"],
            "module_id": row["module_id"], "module_version": row["module_version"],
            "status": row["status"], "request": request,
            "request_sha256": row["request_sha256"], "result_id": row["result_id"],
        }

    def create_episode(self, episode_id: str) -> dict:
        _identifier(episode_id)
        with self._connection(write=True) as connection:
            connection.execute("INSERT OR IGNORE INTO episodes(episode_id) VALUES (?)", (episode_id,))
            return self._episode(connection, episode_id)

    def get_episode(self, episode_id: str) -> dict:
        _identifier(episode_id)
        with self._connection() as connection:
            return self._episode(connection, episode_id)

    def create_job(self, request: dict) -> dict:
        request = validate_request(request)
        encoded = _canonical(request)
        sha = _digest(encoded)
        with self._connection(write=True) as connection:
            self._episode(connection, request["episode_id"])
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (request["job_id"],)).fetchone()
            if row is not None:
                if row["request_sha256"] != sha:
                    raise StorageError("Job identity is already used by a different request")
                return self._job(row)
            connection.execute(
                "INSERT INTO jobs(job_id,episode_id,module_id,module_version,status,request_json,request_sha256) VALUES (?,?,?,?,?,?,?)",
                (request["job_id"], request["episode_id"], request["module_id"], request["module_version"], "queued", encoded, sha),
            )
            return self._job(self._job_row(connection, request["job_id"]))

    def get_job(self, job_id: str) -> dict:
        _identifier(job_id)
        with self._connection() as connection:
            return self._job(self._job_row(connection, job_id))

    def start_job(self, job_id: str) -> dict:
        _identifier(job_id)
        with self._connection(write=True) as connection:
            self._job_row(connection, job_id)
            updated = connection.execute("UPDATE jobs SET status='running' WHERE job_id=? AND status='queued'", (job_id,))
            if updated.rowcount != 1:
                raise TransitionError("Only a queued job can be started")
            return self._job(self._job_row(connection, job_id))

    def complete_job(self, result: dict) -> dict:
        if type(result) is not dict:
            raise StorageError("Result must be an object")
        job_id = _identifier(result.get("job_id"))
        with self._connection(write=True) as connection:
            job = self._job(self._job_row(connection, job_id))
            if job["status"] != "running":
                raise TransitionError("Only a running job can receive a result")
            result = validate_result(result, request=job["request"])
            used_artifact_ids = {
                evidence["artifact_id"]
                for observation in result["observations"]
                for evidence in observation["evidence_refs"]
            }
            declared_artifact_ids = {artifact["artifact_id"] for artifact in result["artifacts"]}
            for artifact in job["request"]["input_artifacts"]:
                if artifact["artifact_id"] in used_artifact_ids and artifact["artifact_id"] not in declared_artifact_ids:
                    result["artifacts"].append(artifact)
            # A persisted result and every frozen snapshot must resolve its
            # evidence independently of a later lookup of the originating job.
            # Both validators copy payloads; the caller's result stays intact.
            result = validate_result(result)
            encoded = _canonical(result)
            if connection.execute("SELECT result_id FROM results WHERE result_id=?", (result["result_id"],)).fetchone() is not None:
                raise StorageError("Result identity is already in use")
            connection.execute(
                "INSERT INTO results(result_id,job_id,episode_id,result_json,result_sha256) VALUES (?,?,?,?,?)",
                (result["result_id"], job_id, result["episode_id"], encoded, _digest(encoded)),
            )
            updated = connection.execute(
                "UPDATE jobs SET status=?,result_id=? WHERE job_id=? AND status='running'",
                (_TERMINAL[result["status"]], result["result_id"], job_id),
            )
            if updated.rowcount != 1:
                raise TransitionError("Job state changed before result publication")
            return self._job(self._job_row(connection, job_id))

    def _result(self, connection, row) -> dict:
        request = self._job(self._job_row(connection, row["job_id"]))["request"]
        result = validate_result(_stored_json(row["result_json"], row["result_sha256"]), request=request)
        if any(result[field] != row[field] for field in ("result_id", "job_id", "episode_id")):
            raise StorageError("Stored result context integrity check failed")
        return result

    def get_result(self, result_id: str) -> dict:
        _identifier(result_id)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM results WHERE result_id=?", (result_id,)).fetchone()
            if row is None:
                raise StorageError("Result was not found")
            return self._result(connection, row)

    def list_results(self, episode_id: str) -> list:
        _identifier(episode_id)
        with self._connection() as connection:
            self._episode(connection, episode_id)
            rows = connection.execute("SELECT * FROM results WHERE episode_id=? ORDER BY result_id", (episode_id,)).fetchall()
            return [self._result(connection, row) for row in rows]

    @staticmethod
    def _snapshot(row) -> dict:
        try:
            snapshot = json.loads(row["snapshot_json"])
            if type(snapshot) is not dict or set(snapshot) != {"schema_version", "snapshot_id", "episode_id", "result_ids", "content_sha256", "results"}:
                raise StorageError("Stored snapshot structure check failed")
            content = {key: value for key, value in snapshot.items() if key not in {"snapshot_id", "content_sha256"}}
            sha = _digest(_canonical(content))
            if (
                sha != row["content_sha256"] or sha != snapshot["content_sha256"]
                or snapshot["snapshot_id"] != "snapshot-" + sha or snapshot["snapshot_id"] != row["snapshot_id"]
                or snapshot["episode_id"] != row["episode_id"]
            ):
                raise StorageError("Stored snapshot integrity check failed")
            return snapshot
        except (TypeError, ValueError, RecursionError):
            raise StorageError("Stored snapshot integrity check failed") from None

    def create_snapshot(self, episode_id: str, result_ids: Optional[list] = None) -> dict:
        _identifier(episode_id)
        if result_ids is not None:
            if type(result_ids) is not list:
                raise StorageError("Snapshot result identifiers must be a list")
            selected = [_identifier(result_id) for result_id in result_ids]
            if len(set(selected)) != len(selected):
                raise StorageError("Snapshot cannot contain duplicate results")
            selected = sorted(selected)
        with self._connection(write=True) as connection:
            self._episode(connection, episode_id)
            if result_ids is None:
                rows = connection.execute("SELECT * FROM results WHERE episode_id=? ORDER BY result_id", (episode_id,)).fetchall()
            else:
                rows = []
                for result_id in selected:
                    row = connection.execute("SELECT * FROM results WHERE result_id=?", (result_id,)).fetchone()
                    if row is None or row["episode_id"] != episode_id:
                        raise StorageError("Snapshot result is unavailable in this episode")
                    rows.append(row)
            results = [self._result(connection, row) for row in rows]
            content = {"schema_version": "1.0", "episode_id": episode_id, "result_ids": [row["result_id"] for row in rows], "results": results}
            sha = _digest(_canonical(content))
            snapshot = dict(content, snapshot_id="snapshot-" + sha, content_sha256=sha)
            prior = connection.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],)).fetchone()
            if prior is not None:
                return self._snapshot(prior)
            connection.execute(
                "INSERT INTO snapshots(snapshot_id,episode_id,content_sha256,snapshot_json) VALUES (?,?,?,?)",
                (snapshot["snapshot_id"], episode_id, sha, _canonical(snapshot)),
            )
            return snapshot

    def get_snapshot(self, snapshot_id: str) -> dict:
        _identifier(snapshot_id)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise StorageError("Snapshot was not found")
            return self._snapshot(row)

    @staticmethod
    def _artifact(connection, episode_id: str, artifact_id: str) -> dict:
        row = connection.execute(
            "SELECT * FROM artifact_catalog WHERE episode_id=? AND artifact_id=?",
            (episode_id, artifact_id),
        ).fetchone()
        if row is None:
            raise StorageError("Artifact was not found in this episode")
        ref = ArtifactStore._validate_ref(_stored_json(row["ref_json"], row["ref_sha256"]))
        if ref["artifact_id"] != row["artifact_id"]:
            raise StorageError("Stored artifact context integrity check failed")
        return ref

    def register_artifact(self, episode_id: str, ref: dict) -> dict:
        """Register a trusted store-produced ref; public callers provide only IDs.

        Content verification belongs to ArtifactStore/upload before this method.
        Equal bytes may have independent catalog links in several episodes.
        """
        _identifier(episode_id)
        ref = ArtifactStore._validate_ref(ref)
        encoded = _canonical(ref)
        sha = _digest(encoded)
        with self._connection(write=True) as connection:
            self._episode(connection, episode_id)
            prior = connection.execute(
                "SELECT * FROM artifact_catalog WHERE episode_id=? AND artifact_id=?",
                (episode_id, ref["artifact_id"]),
            ).fetchone()
            if prior is not None:
                stored = self._artifact(connection, episode_id, ref["artifact_id"])
                if prior["ref_sha256"] != sha:
                    raise ConflictError("Artifact identity is already registered with different metadata")
                return stored
            connection.execute(
                "INSERT INTO artifact_catalog(episode_id,artifact_id,ref_json,ref_sha256) VALUES (?,?,?,?)",
                (episode_id, ref["artifact_id"], encoded, sha),
            )
            return self._artifact(connection, episode_id, ref["artifact_id"])

    def get_artifact(self, episode_id: str, artifact_id: str) -> dict:
        _identifier(episode_id)
        _identifier(artifact_id)
        with self._connection() as connection:
            self._episode(connection, episode_id)
            return self._artifact(connection, episode_id, artifact_id)

    def list_artifacts(self, episode_id: str) -> list:
        _identifier(episode_id)
        with self._connection() as connection:
            self._episode(connection, episode_id)
            rows = connection.execute(
                "SELECT artifact_id FROM artifact_catalog WHERE episode_id=? ORDER BY artifact_id",
                (episode_id,),
            ).fetchall()
            return [self._artifact(connection, episode_id, row["artifact_id"]) for row in rows]

    def list_jobs(self, episode_id: Optional[str] = None, statuses: Optional[list] = None) -> list:
        if episode_id is not None:
            _identifier(episode_id)
        if statuses is not None:
            if type(statuses) not in (list, tuple) or any(type(status) is not str or status not in _JOB_STATUSES for status in statuses):
                raise StorageError("Invalid job status filter")
        with self._connection() as connection:
            if episode_id is not None:
                self._episode(connection, episode_id)
            if statuses is not None and not statuses:
                return []
            where, parameters = [], []
            if episode_id is not None:
                where.append("episode_id=?")
                parameters.append(episode_id)
            if statuses is not None:
                where.append("status IN (" + ",".join("?" for _ in statuses) + ")")
                parameters.extend(statuses)
            query = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY job_id"
            return [self._job(row) for row in connection.execute(query, parameters).fetchall()]

    def _run(self, connection, row) -> dict:
        manifest = validate_manifest(_stored_json(row["manifest_json"], row["manifest_sha256"]))
        job_ids = _stored_json(row["job_ids_json"], row["job_ids_sha256"])
        if (
            manifest["episode_id"] != row["episode_id"] or type(job_ids) is not list
            or len(job_ids) != len(set(_identifier(job_id) for job_id in job_ids))
        ):
            raise StorageError("Stored run context integrity check failed")
        for job_id in job_ids:
            if self._job_row(connection, job_id)["episode_id"] != row["episode_id"]:
                raise StorageError("Stored run job scope check failed")
        report = None
        if row["status"] != "running":
            report = ArtifactStore._validate_ref(_stored_json(row["report_json"], row["report_sha256"]))
            if self._artifact(connection, row["episode_id"], report["artifact_id"]) != report:
                raise StorageError("Stored run report scope check failed")
            snapshot_row = connection.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (row["snapshot_id"],)).fetchone()
            if snapshot_row is None or self._snapshot(snapshot_row)["episode_id"] != row["episode_id"]:
                raise StorageError("Stored run snapshot scope check failed")
        return {
            "schema_version": "1.0", "run_id": row["run_id"], "episode_id": row["episode_id"],
            "idempotency_key": row["idempotency_key"], "manifest": manifest,
            "manifest_sha256": row["manifest_sha256"], "status": row["status"],
            "job_ids": job_ids, "snapshot_id": row["snapshot_id"], "report_artifact": report,
        }

    @staticmethod
    def _run_row(connection, run_id: str):
        row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise StorageError("Workflow run was not found")
        return row

    def create_run(self, episode_id: str, idempotency_key: str, manifest: dict,
                   job_requests: Optional[list] = None, run_id: Optional[str] = None) -> dict:
        _identifier(episode_id)
        _identifier(idempotency_key)
        manifest = validate_manifest(manifest)
        if manifest["episode_id"] != episode_id:
            raise StorageError("Manifest episode does not match run context")
        if run_id is not None:
            _identifier(run_id)
        encoded = _canonical(manifest)
        sha = _digest(encoded)
        with self._connection(write=True) as connection:
            self._episode(connection, episode_id)
            prior = connection.execute(
                "SELECT * FROM workflow_runs WHERE episode_id=? AND idempotency_key=?",
                (episode_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                existing = self._run(connection, prior)
                if prior["manifest_sha256"] != sha:
                    raise ConflictError("Idempotency key is already used by a different manifest")
                return existing
            if job_requests is None:
                job_requests = []
            if type(job_requests) is not list:
                raise StorageError("Workflow job requests must be a list")
            requests = [validate_request(request) for request in job_requests]
            if len(requests) != len(manifest["modules"]):
                raise StorageError("Every selected module requires exactly one job request")
            by_module = {request["module_id"]: request for request in requests}
            job_ids = [request["job_id"] for request in requests]
            if len(by_module) != len(requests) or len(set(job_ids)) != len(job_ids):
                raise StorageError("Workflow cannot contain duplicate jobs or modules")
            for selected in manifest["modules"]:
                request = by_module.get(selected["module_id"])
                if request is None or (
                    request["episode_id"] != episode_id
                    or request["module_version"] != selected["module_version"]
                    or _canonical(request["parameters"]) != _canonical(selected["parameters"])
                    or request["protocol_id"] != manifest["protocol_id"]
                    or [ref["artifact_id"] for ref in request["input_artifacts"]] != selected["input_artifact_ids"]
                ):
                    raise StorageError("Workflow job does not match its manifest selection")
                for ref in request["input_artifacts"]:
                    if self._artifact(connection, episode_id, ref["artifact_id"]) != ref:
                        raise StorageError("Workflow input metadata does not match its catalog entry")
            link_id = manifest["linkage_evidence_artifact_id"]
            if link_id is not None:
                self._artifact(connection, episode_id, link_id)
            for result_id in manifest["reuse_result_ids"]:
                result_row = connection.execute("SELECT * FROM results WHERE result_id=?", (result_id,)).fetchone()
                if result_row is None or result_row["episode_id"] != episode_id:
                    raise StorageError("Reused result was not found in this episode")
                self._result(connection, result_row)
            run_id = run_id or "run-" + uuid.uuid4().hex
            if connection.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone() is not None:
                raise ConflictError("Workflow run identity is already in use")
            for request in requests:
                if connection.execute("SELECT job_id FROM jobs WHERE job_id=?", (request["job_id"],)).fetchone() is not None:
                    raise ConflictError("Workflow job identity is already in use")
                request_json = _canonical(request)
                connection.execute(
                    "INSERT INTO jobs(job_id,episode_id,module_id,module_version,status,request_json,request_sha256) VALUES (?,?,?,?,?,?,?)",
                    (request["job_id"], episode_id, request["module_id"], request["module_version"], "queued", request_json, _digest(request_json)),
                )
            job_json = _canonical(job_ids)
            connection.execute(
                "INSERT INTO workflow_runs(run_id,episode_id,idempotency_key,manifest_json,manifest_sha256,status,job_ids_json,job_ids_sha256) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, episode_id, idempotency_key, encoded, sha, "running", job_json, _digest(job_json)),
            )
            return self._run(connection, self._run_row(connection, run_id))

    def get_run(self, run_id: str) -> dict:
        _identifier(run_id)
        with self._connection() as connection:
            return self._run(connection, self._run_row(connection, run_id))

    def list_pending_runs(self) -> list:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM workflow_runs WHERE status='running' ORDER BY run_id").fetchall()
            return [self._run(connection, row) for row in rows]

    def finalize_run(self, run_id: str, snapshot_id: str, report_ref: dict, status: str) -> dict:
        _identifier(run_id)
        _identifier(snapshot_id)
        if type(status) is not str or status not in _RUN_TERMINAL:
            raise StorageError("Invalid terminal workflow status")
        report_ref = ArtifactStore._validate_ref(report_ref)
        if report_ref["role"] != "derived":
            raise StorageError("Workflow report must be a derived artifact")
        with self._connection(write=True) as connection:
            row = self._run_row(connection, run_id)
            current = self._run(connection, row)
            if row["status"] != "running":
                return current
            episode_id = row["episode_id"]
            snapshot_row = connection.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if snapshot_row is None or self._snapshot(snapshot_row)["episode_id"] != episode_id:
                raise StorageError("Workflow snapshot was not found in this episode")
            if self._artifact(connection, episode_id, report_ref["artifact_id"]) != report_ref:
                raise StorageError("Workflow report metadata does not match its catalog entry")
            encoded = _canonical(report_ref)
            connection.execute(
                "UPDATE workflow_runs SET status=?,snapshot_id=?,report_json=?,report_sha256=? WHERE run_id=? AND status='running'",
                (status, snapshot_id, encoded, _digest(encoded), run_id),
            )
            return self._run(connection, self._run_row(connection, run_id))
