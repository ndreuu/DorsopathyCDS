"""Provider boundary; importing it never loads imaging or ML dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class ArtifactAccess(Protocol):
    def add_bytes(self, data: bytes, media_type: str, role: str = "source") -> dict[str, Any]:
        ...

    def read(self, reference: dict[str, Any]) -> bytes:
        ...

    def add_file(self, path: Path, media_type: str, role: str = "source") -> dict[str, Any]:
        ...

    def open(self, reference: dict[str, Any]) -> BinaryIO:
        """Return a verified, rewound file; the caller closes it with `with`."""
        ...


@dataclass(frozen=True)
class ProviderSpec:
    module_id: str
    module_version: str
    display_name: str
    synthetic_only: bool = False


@dataclass(frozen=True)
class ProviderContext:
    artifacts: ArtifactAccess


class ProviderProtocol(Protocol):
    """An independently implemented producer of versioned observations."""

    spec: ProviderSpec

    def analyze(self, request: dict[str, Any], context: ProviderContext) -> dict[str, Any]:
        ...
