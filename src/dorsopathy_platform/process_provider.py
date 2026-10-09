"""Trusted subprocess provider: isolated files, bounded JSON, no shell or ML import."""

from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

from .contracts import ContractError, validate_request, validate_result
from .ports import ProviderSpec
from .process_io import read_worker_output, safe_exchange_path, write_worker_request


class ProcessExecutionError(RuntimeError):
    """Public enum only; worker output and exception strings are never retained."""

    CODES = frozenset({"worker_timeout", "worker_exit_failed", "worker_start_failed", "worker_interrupted"})

    def __init__(self, code):
        if code not in self.CODES:
            raise ValueError("Unknown worker error code")
        self.code = code
        super().__init__("Module worker execution failed")


def _terminate_group(process):
    """Terminate the private POSIX session even if its leader already exited."""
    with process._platform_cleanup_lock:
        if process._platform_cleanup_done:
            return
        # Avoid signalling a reused PID on repeated finally/shutdown cleanup.
        process._platform_cleanup_done = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # This is an OS cleanup failure, not a successful inference.
            raise ProcessExecutionError("worker_interrupted") from None


class ProcessProvider:
    def __init__(self, spec, command, workspace_root, timeout_seconds=600):
        if not isinstance(spec, ProviderSpec):
            raise ValueError("Worker requires a ProviderSpec")
        if type(command) is not tuple or not command or any(type(arg) is not str or not arg or "\0" in arg for arg in command):
            raise ValueError("Worker command must be a trusted argv tuple")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Worker timeout must be positive and finite")
        if os.name != "posix":
            raise ValueError("Process group cleanup currently requires POSIX")
        root = Path(workspace_root)
        if root.is_symlink():
            raise ValueError("Worker workspace root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        self.spec = spec
        self.command = command
        self.workspace_root = root.resolve()
        self.timeout_seconds = float(timeout_seconds)
        self._lock = threading.Lock()
        self._active = set()
        self._closed = False

    def analyze(self, request, context):
        request = validate_request(request)
        if (request["module_id"], request["module_version"]) != (self.spec.module_id, self.spec.module_version):
            raise ContractError("Worker request does not match its configured provider")
        with self._lock:
            if self._closed:
                raise ProcessExecutionError("worker_interrupted")
        with tempfile.TemporaryDirectory(prefix="job-" + request["job_id"] + "-", dir=self.workspace_root) as directory:
            exchange_dir = Path(directory)
            (exchange_dir / "inputs").mkdir(mode=0o700)
            inputs = []
            for reference in request["input_artifacts"]:
                relative_path = "inputs/" + reference["sha256"]
                path = safe_exchange_path(exchange_dir, relative_path)
                with context.artifacts.open(reference) as source, path.open("xb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                path.chmod(0o400)
                inputs.append({"artifact_id": reference["artifact_id"], "relative_path": relative_path})
            write_worker_request(exchange_dir, request, inputs)
            process = None
            try:
                with self._lock:
                    if self._closed:
                        raise ProcessExecutionError("worker_interrupted")
                    try:
                        process = subprocess.Popen(
                            self.command + ("--exchange-dir", str(exchange_dir)),
                            cwd=exchange_dir, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            shell=False, close_fds=True, start_new_session=True,
                        )
                    except OSError:
                        raise ProcessExecutionError("worker_start_failed") from None
                    process._platform_cleanup_lock = threading.Lock()
                    process._platform_cleanup_done = False
                    self._active.add(process)
                try:
                    return_code = process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    raise ProcessExecutionError("worker_timeout") from None
                with self._lock:
                    interrupted = self._closed
                if interrupted:
                    raise ProcessExecutionError("worker_interrupted")
                if return_code != 0:
                    raise ProcessExecutionError("worker_exit_failed")
                # Stop remaining children before trusting any output files.
                _terminate_group(process)
                envelope = read_worker_output(exchange_dir, request)
                result = deepcopy(envelope["result"])
                if result["status"] == "failed":
                    # Worker error/warning text may contain source-data details.
                    result["error"] = {
                        "code": "worker_reported_failure",
                        "message": "Module worker reported failure; no clinical inference was produced.",
                    }
                    result["warnings"] = []
                imported = {}
                for descriptor in envelope["derived"]:
                    reference = context.artifacts.add_file(
                        safe_exchange_path(exchange_dir, descriptor["relative_path"]),
                        descriptor["media_type"], role="derived",
                    )
                    if any(reference[field] != descriptor[field] for field in ("artifact_id", "sha256", "size_bytes", "media_type", "role")):
                        raise ContractError("Imported worker artifact failed its integrity check")
                    imported[reference["artifact_id"]] = reference
                result["artifacts"] = [imported.get(ref["artifact_id"], ref) for ref in result["artifacts"]]
                return validate_result(result, request)
            finally:
                if process is not None:
                    try:
                        _terminate_group(process)
                    finally:
                        with self._lock:
                            self._active.discard(process)

    def close(self):
        """Prevent new work and stop every owned worker and remaining child."""
        with self._lock:
            self._closed = True
            processes = list(self._active)
        cleanup_failed = False
        for process in processes:
            try:
                _terminate_group(process)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise ProcessExecutionError("worker_interrupted") from None

    shutdown = close
