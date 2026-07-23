"""Composition roots for local/demo troubleshooting modes."""

from .fixtures import (
    FixtureEvidenceCollector,
    fixture_incident_903001,
    fixture_sop_903001,
)
from .orchestrator import TroubleshootingOrchestrator
from .repository import InMemorySopRepository


def build_fixture_orchestrator(scenario: str = "saturated") -> TroubleshootingOrchestrator:
    return TroubleshootingOrchestrator(
        sop_repository=InMemorySopRepository([fixture_sop_903001()]),
        evidence_collector=FixtureEvidenceCollector(scenario=scenario),
    )


__all__ = ["build_fixture_orchestrator", "fixture_incident_903001"]
