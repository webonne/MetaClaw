"""Replaceable tool ports; MCP adapters can implement these protocols later."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import Diagnosis, EvidenceQuery, EvidenceResult, IncidentContext, SopEntry


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


class SopRepository(Protocol):
    def get(self, system: str, error_code: str) -> SopEntry | None: ...


class EvidenceCollector(Protocol):
    mode: str

    def collect(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult: ...
