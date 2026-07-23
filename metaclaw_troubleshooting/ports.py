"""Replaceable tool ports; MCP adapters can implement these protocols later."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import List, Protocol

from .models import (
    Diagnosis,
    EvidenceQuery,
    EvidenceResult,
    IncidentContext,
    KnowledgePublication,
    SopEntry,
)


class RepositoryUnavailable(RuntimeError):
    """Raised when diagnosis state cannot be read or committed safely."""

    code = "storage_unavailable"


@dataclass(frozen=True)
class RepositoryReadiness:
    ready: bool
    adapter: str
    persistent: bool
    schema_version: int | None
    pending_publications: int | None
    detail: str | None = None


class DiagnosisRepository(Protocol):
    """Persistence seam for diagnosis state and composite idempotency."""

    def add(self, diagnosis: Diagnosis) -> Diagnosis: ...

    def update(
        self,
        diagnosis_id: str,
        mutation: Callable[[Diagnosis], Diagnosis],
    ) -> Diagnosis | None: ...

    def get(self, diagnosis_id: str) -> Diagnosis | None: ...

    def list(self, *, include_rehearsals: bool = False) -> list[Diagnosis]: ...

    def pending_publications(self, *, limit: int = 100) -> List[KnowledgePublication]: ...

    def claim_publications(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> List[KnowledgePublication]: ...

    def mark_publication_published(self, publication_id: str, *, worker_id: str) -> bool: ...

    def mark_publication_failed(
        self,
        publication_id: str,
        *,
        worker_id: str,
        error: str,
    ) -> bool: ...

    def readiness(self) -> RepositoryReadiness: ...


class SopRepository(Protocol):
    def get(self, system: str, error_code: str) -> SopEntry | None: ...


class EvidenceCollector(Protocol):
    mode: str

    def collect(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult: ...
