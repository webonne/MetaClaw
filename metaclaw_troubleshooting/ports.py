"""Replaceable tool ports; MCP adapters can implement these protocols later."""

from __future__ import annotations

from typing import Protocol

from .models import EvidenceQuery, EvidenceResult, IncidentContext, SopEntry


class SopRepository(Protocol):
    def get(self, system: str, error_code: str) -> SopEntry | None: ...


class EvidenceCollector(Protocol):
    mode: str

    def collect(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult: ...
