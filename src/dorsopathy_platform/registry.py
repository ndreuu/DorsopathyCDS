"""Explicit provider registration, independent of clinical rules."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .contracts import validate_request
from .ports import ProviderProtocol, ProviderSpec


class RegistryError(ValueError):
    pass


class ModuleRegistry:
    def __init__(self) -> None:
        self._providers: dict[tuple[str, str], ProviderProtocol] = {}

    def register(self, provider: ProviderProtocol) -> None:
        spec = getattr(provider, "spec", None)
        if not isinstance(spec, ProviderSpec) or not callable(getattr(provider, "analyze", None)):
            raise RegistryError("Provider requires a specification and analyze method")
        if type(spec.display_name) is not str or not spec.display_name.strip() or type(spec.synthetic_only) is not bool:
            raise RegistryError("Invalid provider specification")
        validate_request({
            "schema_version": "1.0", "job_id": "registry-check", "episode_id": "registry-check",
            "module_id": spec.module_id, "module_version": spec.module_version,
            "input_artifacts": [], "parameters": {}, "protocol_id": None,
        })
        key = (spec.module_id, spec.module_version)
        if key in self._providers:
            raise RegistryError("Provider version is already registered")
        self._providers[key] = provider

    def get(self, module_id: str, module_version: str) -> ProviderProtocol:
        try:
            return self._providers[(module_id, module_version)]
        except KeyError as exc:
            raise RegistryError("Requested provider version is not registered") from exc

    def describe(self) -> list[dict[str, Any]]:
        return [asdict(self._providers[key].spec) for key in sorted(self._providers)]
