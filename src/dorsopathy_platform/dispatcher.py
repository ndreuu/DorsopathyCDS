"""Long-lived persisted-job dispatcher with separate CPU and single-MRI lanes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from .orchestration import JobRunner
from .results import result_envelope
from .storage import TransitionError


class Dispatcher:
    def __init__(self, repository, artifacts, registry, max_workers=4,
                 mri_module_ids=("mri.anatomy",), recover=True):
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("Expected 1 to 16 CPU workers")
        if type(mri_module_ids) is not tuple or any(type(item) is not str or not item for item in mri_module_ids):
            raise ValueError("MRI lane requires a tuple of configured module IDs")
        if type(recover) is not bool:
            raise ValueError("Recovery option must be boolean")
        self.repository = repository
        self.artifacts = artifacts
        self.registry = registry
        self.runner = JobRunner(repository, artifacts, registry)
        self._mri_module_ids = frozenset(mri_module_ids)
        self._lock = threading.Lock()
        self._closed = False
        self._futures = {}
        self._cpu = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dorsopathy-cpu")
        self._mri = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dorsopathy-mri")
        try:
            if recover:
                self._recover()
        except BaseException:
            self.close()
            raise

    def _recover(self):
        # The server exclusively owns this repository's dispatcher lifecycle.
        for job in self.repository.list_jobs(statuses=("running",)):
            result = result_envelope(job["request"], [], [], status="failed")
            result["error"] = {
                "code": "worker_interrupted",
                "message": "Previous module processing was interrupted; no clinical inference was produced.",
            }
            try:
                self.repository.complete_job(result)
            except TransitionError:
                # Another terminal transition already won; never rewrite it.
                pass
        for job in self.repository.list_jobs(statuses=("queued",)):
            self.enqueue(job["job_id"])

    def enqueue(self, job_id):
        """Deliver an already-persisted job; return immediately with its Future.

        A locally pending delivery returns the same Future. Terminal/running
        jobs return None; storage CAS arbitrates deliveries across dispatchers.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Dispatcher is closed")
            if job_id in self._futures:
                return self._futures[job_id]
            job = self.repository.get_job(job_id)
            if job["status"] != "queued":
                return None
            lane = self._mri if job["module_id"] in self._mri_module_ids else self._cpu
            future = lane.submit(self._execute, job_id)
            self._futures[job_id] = future
        # Register outside the lock: a completed Future invokes this immediately.
        future.add_done_callback(lambda done: self._forget(job_id, done))
        return future

    def _forget(self, job_id, future):
        with self._lock:
            if self._futures.get(job_id) is future:
                del self._futures[job_id]

    def _execute(self, job_id):
        try:
            return self.runner.execute(job_id)
        except TransitionError:
            # Duplicate delivery cannot execute the provider after a failed CAS.
            return None

    def close(self, wait=True):
        """Stop admission, cancel undelivered jobs, and clean subprocess workers.

        Cancelled persisted jobs stay queued for startup recovery. Already
        running in-process CPU calls cannot be forcibly interrupted; wait=True
        joins them. ProcessProvider.close always kills its owned process group.
        """
        if type(wait) is not bool:
            raise ValueError("Shutdown wait option must be boolean")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._futures.values())
        for future in pending:
            future.cancel()
        cleanup_failed = False
        try:
            for spec in self.registry.describe():
                provider = self.registry.get(spec["module_id"], spec["module_version"])
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        # One broken cleanup must not abandon other workers.
                        cleanup_failed = True
        finally:
            self._cpu.shutdown(wait=wait, cancel_futures=True)
            self._mri.shutdown(wait=wait, cancel_futures=True)
        if cleanup_failed:
            raise RuntimeError("One or more provider workers could not be cleaned up") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
