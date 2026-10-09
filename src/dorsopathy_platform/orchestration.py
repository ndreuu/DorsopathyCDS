"""Single-job execution; admission and process isolation live in separate modules."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from .contracts import ContractError, validate_request, validate_result
from .ports import ProviderContext
from .registry import ModuleRegistry, RegistryError
from .process_provider import ProcessExecutionError
from .results import result_envelope
from .storage import ArtifactStore, SqliteRepository


class JobRunner:
    def __init__(self, repository: SqliteRepository, artifacts: ArtifactStore, registry: ModuleRegistry) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.registry = registry

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        request = validate_request(request)
        self.registry.get(request["module_id"], request["module_version"])
        return self.repository.create_job(request)

    def execute(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        request = job["request"]
        # CAS belongs to storage: concurrent attempts cannot run the same job.
        self.repository.start_job(job_id)
        try:
            provider = self.registry.get(request["module_id"], request["module_version"])
            for reference in request["input_artifacts"]:
                with self.artifacts.open(reference):
                    pass
            result = validate_result(provider.analyze(deepcopy(request), ProviderContext(self.artifacts)), request)
            if provider.spec.synthetic_only and any(
                observation["origin"] != "synthetic_demo" for observation in result["observations"]
            ):
                raise ContractError("Synthetic provider cannot emit non-synthetic observations")
            # Make persisted results self-contained for evidence resolution.
            known = {reference["artifact_id"] for reference in result["artifacts"]}
            required = {
                ref["artifact_id"] for observation in result["observations"]
                for ref in observation["evidence_refs"]
            }
            for reference in request["input_artifacts"]:
                if reference["artifact_id"] in required and reference["artifact_id"] not in known:
                    result["artifacts"].append(deepcopy(reference))
                    known.add(reference["artifact_id"])
            for reference in result["artifacts"]:
                with self.artifacts.open(reference):
                    pass
            result = validate_result(result, request)
        except Exception as exc:
            # Do not store exception strings: providers may include source data.
            result = result_envelope(request, [], [], status="failed")
            code = (
                exc.code if isinstance(exc, ProcessExecutionError)
                else "provider_unavailable" if isinstance(exc, RegistryError)
                else "invalid_module_result" if isinstance(exc, ContractError)
                else "provider_execution_failed"
            )
            result["error"] = {
                "code": code,
                "message": "Module processing failed; no clinical inference was produced.",
            }
            result = validate_result(result, request)
        # Storage failures remain infrastructure errors, not fake module success.
        self.repository.complete_job(result)
        return self.repository.get_result(result["result_id"])

    def execute_many(self, job_ids: list[str], max_workers: int = 2) -> list[dict[str, Any]]:
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("Duplicate jobs in one execution batch")
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("Expected 1 to 16 local demo workers")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.execute, job_id) for job_id in job_ids]
            return [future.result() for future in futures]
