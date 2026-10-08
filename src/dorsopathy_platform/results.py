"""Dependency-free result construction helpers for provider implementations."""

from __future__ import annotations

import hashlib
from typing import Any


def result_id_for(job_id: str) -> str:
    return "result-" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:32]


def observation_id_for(job_id: str, concept: str, instance_key: str = "") -> str:
    """Disambiguate repeated concepts with a stable level/side/ROI instance key."""
    identity = job_id + "\0" + concept
    if instance_key:
        identity += "\0" + instance_key
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "observation-" + digest[:32]


def result_envelope(
    request: dict[str, Any], observations: list[dict[str, Any]],
    artifacts: list[dict[str, Any]], status: str = "complete",
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Construct identity metadata; providers still own observations and QC."""
    return {
        "schema_version": "1.0", "result_id": result_id_for(request["job_id"]),
        "job_id": request["job_id"], "episode_id": request["episode_id"],
        "module_id": request["module_id"], "module_version": request["module_version"],
        "status": status, "observations": observations, "artifacts": artifacts,
        "warnings": warnings or [], "error": None,
    }
