"""FastAPI surface for the 903001 troubleshooting vertical slice."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse

from .factory import build_fixture_orchestrator, fixture_incident_903001
from .models import (
    ActionType,
    ActorRequest,
    ApprovalRequest,
    ApprovalStatus,
    Diagnosis,
    DiagnosisStatus,
    IncidentContext,
    TimelineEvent,
)
from .orchestrator import TroubleshootingOrchestrator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InMemoryDiagnosisStore:
    def __init__(self):
        self._items: dict[str, Diagnosis] = {}
        self._idempotency_keys: dict[tuple[str, str, str, str], str] = {}

    def put(self, diagnosis: Diagnosis) -> Diagnosis:
        existing = self._items.get(diagnosis.diagnosis_id)
        if existing is not None:
            return existing
        idempotency_key = self._idempotency_key(diagnosis)
        if idempotency_key is not None:
            existing_id = self._idempotency_keys.get(idempotency_key)
            if existing_id is not None:
                return self._items[existing_id]
        self._items[diagnosis.diagnosis_id] = diagnosis
        if idempotency_key is not None:
            self._idempotency_keys[idempotency_key] = diagnosis.diagnosis_id
        return diagnosis

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        return self._items.get(diagnosis_id)

    def list(self) -> list[Diagnosis]:
        return list(self._items.values())

    @staticmethod
    def _idempotency_key(
        diagnosis: Diagnosis,
    ) -> tuple[str, str, str, str] | None:
        incident = diagnosis.incident
        error_code = (incident.error_code or "").strip()
        if not error_code:
            return None
        occurred_at = incident.occurred_at
        try:
            occurred = (
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                if occurred_at
                else datetime.now(timezone.utc)
            )
        except ValueError:
            occurred = datetime.now(timezone.utc)
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        occurred = occurred.astimezone(timezone.utc)
        bucket_minute = occurred.minute - occurred.minute % 5
        bucket = occurred.replace(
            minute=bucket_minute,
            second=0,
            microsecond=0,
        ).isoformat()
        return (
            incident.system.strip().lower(),
            error_code,
            incident.service.strip().lower(),
            bucket,
        )


def create_app(
    orchestrator: TroubleshootingOrchestrator | None = None,
    *,
    seed_demo: bool = True,
    workbench_path: Path | None = None,
) -> FastAPI:
    orchestrator = orchestrator or build_fixture_orchestrator()
    store = InMemoryDiagnosisStore()
    if seed_demo:
        store.put(orchestrator.diagnose(fixture_incident_903001()))

    app = FastAPI(
        title="MetaClaw Intelligent Troubleshooting MVP",
        version="0.1.0",
    )
    app.state.orchestrator = orchestrator
    app.state.diagnoses = store
    app.state.write_execution_enabled = False
    resolved_workbench = workbench_path or (
        Path(__file__).resolve().parents[1] / "docs" / "intelligent-troubleshooting" / "console-workbench.html"
    )

    def get_diagnosis(diagnosis_id: str) -> Diagnosis:
        diagnosis = store.get(diagnosis_id)
        if diagnosis is None:
            raise HTTPException(status_code=404, detail="diagnosis not found")
        return diagnosis

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/workbench")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "ok": True,
            "mode": orchestrator.mode,
            "write_execution_enabled": False,
        }

    @app.get("/workbench", response_class=HTMLResponse, include_in_schema=False)
    async def workbench() -> HTMLResponse:
        if not resolved_workbench.is_file():
            raise HTTPException(status_code=404, detail="workbench HTML not found")
        return HTMLResponse(resolved_workbench.read_text(encoding="utf-8"))

    @app.get("/v1/troubleshooting/diagnoses", response_model=list[Diagnosis])
    async def list_diagnoses() -> list[Diagnosis]:
        return store.list()

    @app.post(
        "/v1/troubleshooting/diagnoses",
        response_model=Diagnosis,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_diagnosis(incident: IncidentContext) -> Diagnosis:
        return store.put(orchestrator.diagnose(incident))

    @app.get("/v1/troubleshooting/diagnoses/{diagnosis_id}", response_model=Diagnosis)
    async def read_diagnosis(diagnosis_id: str) -> Diagnosis:
        return get_diagnosis(diagnosis_id)

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
        response_model=Diagnosis,
    )
    async def confirm_diagnosis(diagnosis_id: str, request: ActorRequest) -> Diagnosis:
        diagnosis = get_diagnosis(diagnosis_id)
        if diagnosis.abstained:
            raise HTTPException(
                status_code=409,
                detail="abstained diagnosis requires new evidence before confirmation",
            )
        diagnosis.status = DiagnosisStatus.CONFIRMED
        diagnosis.timeline.append(
            TimelineEvent(
                timestamp=_now(),
                event="人工确认诊断结论",
                actor=request.actor,
            )
        )
        return diagnosis

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{action_id}/approve",
        response_model=Diagnosis,
    )
    async def approve_action(
        diagnosis_id: str,
        action_id: str,
        request: ApprovalRequest,
    ) -> Diagnosis:
        diagnosis = get_diagnosis(diagnosis_id)
        action = next(
            (item for item in diagnosis.recommended_actions if item.action_id == action_id),
            None,
        )
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        if action.action_type != ActionType.MANUAL_WRITE or not action.requires_approval:
            raise HTTPException(status_code=400, detail="action does not require approval")
        action.approval_status = ApprovalStatus.APPROVED
        diagnosis.pending_writes = [item for item in diagnosis.pending_writes if item.action_id != action_id]
        diagnosis.timeline.append(
            TimelineEvent(
                timestamp=_now(),
                event=f"生产写操作已人工批准（{request.reason}）",
                actor=request.actor,
            )
        )
        return diagnosis

    @app.post("/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{action_id}/execute")
    async def execute_action(diagnosis_id: str, action_id: str) -> None:
        diagnosis = get_diagnosis(diagnosis_id)
        if not any(item.action_id == action_id for item in diagnosis.recommended_actions):
            raise HTTPException(status_code=404, detail="action not found")
        raise HTTPException(
            status_code=409,
            detail="production write executor is not connected in this MVP",
        )

    return app


app = create_app()
