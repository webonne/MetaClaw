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


def build_rehearsal_orchestrator_903001() -> TroubleshootingOrchestrator:
    """Build an approved synthetic SOP isolated from the canonical CSDP route."""

    sop = fixture_sop_903001().model_copy(deep=True)
    sop.system = "CSDP-REHEARSAL"
    sop.status = "approved"
    sop.verified = True
    return TroubleshootingOrchestrator(
        sop_repository=InMemorySopRepository([sop]),
        evidence_collector=FixtureEvidenceCollector(scenario="saturated"),
    )


__all__ = [
    "build_fixture_orchestrator",
    "build_rehearsal_orchestrator_903001",
    "fixture_incident_903001",
]
