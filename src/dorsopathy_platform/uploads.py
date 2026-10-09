"""Bounded single-file uploads and episode-scoped verified downloads."""

from contextlib import contextmanager
import os
from typing import BinaryIO, Optional
import uuid

from .storage import ArtifactStore, SqliteRepository, StorageError


class UploadError(StorageError):
    """A failed upload; code distinguishes configured size rejection for HTTP."""

    def __init__(self, message: str, code: str = "invalid_upload"):
        super().__init__(message)
        self.code = code


class UploadService:
    """Spool to a private file, import atomically, then publish a catalog link.

    The caller owns the input stream; upload never closes it. Returned download
    streams are caller-owned and must be closed (prefer a with statement).
    Failure may leave an unlinked deduplicated blob, never a catalog entry.
    """

    CHUNK_BYTES = 64 * 1024

    def __init__(self, repository: SqliteRepository, artifacts: ArtifactStore,
                 max_bytes: int = 512 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes < 0:
            raise UploadError("Invalid upload size configuration")
        self.repository = repository
        self.artifacts = artifacts
        self.max_bytes = max_bytes
        self._temporary_root = artifacts.root / "uploads"

    @contextmanager
    def _directory(self):
        descriptor = None
        try:
            self._temporary_root.mkdir(mode=0o700, exist_ok=True)
            descriptor = os.open(str(self._temporary_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            yield descriptor
        except OSError:
            raise UploadError("Upload staging storage is unavailable or unsafe") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def upload(self, episode_id: str, stream: BinaryIO, media_type: str,
               declared_size: Optional[int] = None, role: str = "source") -> dict:
        self.repository.get_episode(episode_id)
        try:
            ArtifactStore._validate_metadata(media_type, role)
        except StorageError:
            raise UploadError("Invalid upload metadata") from None
        if declared_size is not None:
            if type(declared_size) is not int or declared_size < 0:
                raise UploadError("Invalid declared upload size")
            if declared_size > self.max_bytes:
                raise UploadError("Upload exceeds configured size limit", "upload_too_large")
        if not callable(getattr(stream, "read", None)):
            raise UploadError("Upload requires a binary stream")
        with self._directory() as directory:
            temporary = ".upload-" + uuid.uuid4().hex + ".pending"
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                size = 0
                with os.fdopen(descriptor, "wb") as destination:
                    while True:
                        # One byte beyond the limit distinguishes exact EOF
                        # from overflow without accepting an unbounded buffer.
                        read_size = min(self.CHUNK_BYTES, self.max_bytes - size + 1)
                        chunk = stream.read(read_size)
                        if type(chunk) is not bytes or len(chunk) > read_size:
                            raise UploadError("Upload stream returned invalid binary data")
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise UploadError("Upload exceeds configured size limit", "upload_too_large")
                        if declared_size is not None and size > declared_size:
                            raise UploadError("Upload does not match its declared size")
                        destination.write(chunk)
                    if declared_size is not None and size != declared_size:
                        raise UploadError("Upload ended before its declared size")
                    destination.flush()
                    os.fsync(destination.fileno())
                ref = self.artifacts.add_file(self._temporary_root / temporary, media_type, role=role)
                return self.repository.register_artifact(episode_id, ref)
            except UploadError:
                raise
            except (OSError, ValueError, TypeError):
                raise UploadError("Upload could not be completed") from None
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def open(self, episode_id: str, artifact_id: str):
        ref = self.repository.get_artifact(episode_id, artifact_id)
        return ref, self.artifacts.open(ref)
