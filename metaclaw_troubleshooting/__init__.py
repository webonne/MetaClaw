"""MetaClaw-based intelligent troubleshooting MVP.

This package is intentionally separate from the MetaClaw training/runtime
kernel.  It talks to MetaClaw through an adapter boundary when an LLM fallback
is introduced, while deterministic troubleshooting remains independently
testable.
"""

from .factory import build_fixture_orchestrator, fixture_incident_903001
from .models import Diagnosis, IncidentContext, SopEntry
from .module import TroubleshootingModule
from .orchestrator import TroubleshootingOrchestrator

__all__ = [
    "Diagnosis",
    "IncidentContext",
    "SopEntry",
    "TroubleshootingModule",
    "TroubleshootingOrchestrator",
    "build_fixture_orchestrator",
    "fixture_incident_903001",
]
