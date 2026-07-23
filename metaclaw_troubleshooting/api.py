"""FastAPI surface for the 903001 troubleshooting vertical slice."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .factory import (
    build_fixture_orchestrator,
    build_rehearsal_orchestrator_903001,
    fixture_incident_903001,
)
from .models import (
    ActionOutcomeRequest,
    ActorRequest,
    ApprovalRequest,
    CloseRequest,
    Diagnosis,
    IncidentContext,
    TransferRequest,
)
from .module import (
    ApproveAction,
    CloseDiagnosis,
    ConfirmDiagnosis,
    DiagnosisNotFound,
    ExecuteAction,
    InvalidAction,
    ProductionWriteDisabled,
    RecordActionOutcome,
    TransferDiagnosis,
    TroubleshootingModule,
    TroubleshootingNotFound,
)
from .orchestrator import TroubleshootingOrchestrator
from .ports import DiagnosisRepository, RepositoryReadiness, RepositoryUnavailable
from .repository import InMemoryDiagnosisRepository, SQLiteDiagnosisRepository
from .workflow import WorkflowConflict

logger = logging.getLogger(__name__)


def create_app(
    orchestrator: TroubleshootingOrchestrator | None = None,
    *,
    seed_demo: bool = True,
    workbench_path: Path | None = None,
    diagnosis_repository: DiagnosisRepository | None = None,
    database_path: str | Path | None = None,
) -> FastAPI:
    if diagnosis_repository is not None and database_path is not None:
        raise ValueError("pass diagnosis_repository or database_path, not both")
    orchestrator = orchestrator or build_fixture_orchestrator()
    owns_repository = diagnosis_repository is None
    if diagnosis_repository is not None:
        diagnoses = diagnosis_repository
    elif database_path is not None:
        diagnoses = SQLiteDiagnosisRepository(database_path)
    else:
        diagnoses = InMemoryDiagnosisRepository()
    module = TroubleshootingModule(orchestrator=orchestrator, diagnoses=diagnoses)
    rehearsal_module = TroubleshootingModule(
        orchestrator=build_rehearsal_orchestrator_903001(),
        diagnoses=diagnoses,
    )
    if seed_demo:
        module.diagnose(fixture_incident_903001())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if owns_repository:
            close = getattr(diagnoses, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="MetaClaw Intelligent Troubleshooting MVP",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.troubleshooting = module
    app.state.troubleshooting_rehearsal = rehearsal_module
    app.state.diagnosis_repository = diagnoses
    app.state.write_execution_enabled = False
    resolved_workbench = workbench_path or resources.files("metaclaw_troubleshooting").joinpath(
        "static",
        "console-workbench.html",
    )

    def storage_payload(readiness: RepositoryReadiness) -> dict[str, object]:
        return {
            "ready": readiness.ready,
            "adapter": readiness.adapter,
            "persistent": readiness.persistent,
            "schema_version": readiness.schema_version,
            "pending_publications": readiness.pending_publications,
            "detail": readiness.detail,
        }

    def get_diagnosis(diagnosis_id: str) -> Diagnosis:
        diagnosis = module.get(diagnosis_id)
        if diagnosis is None:
            raise DiagnosisNotFound("diagnosis not found")
        return diagnosis

    @app.exception_handler(WorkflowConflict)
    async def workflow_conflict_handler(
        _request: Request,
        error: WorkflowConflict,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": error.code, "detail": str(error)},
        )

    @app.exception_handler(ProductionWriteDisabled)
    async def production_write_disabled_handler(
        _request: Request,
        error: ProductionWriteDisabled,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": error.code, "detail": str(error)},
        )

    @app.exception_handler(TroubleshootingNotFound)
    async def not_found_handler(
        _request: Request,
        error: TroubleshootingNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": error.code, "detail": str(error)},
        )

    @app.exception_handler(InvalidAction)
    async def invalid_action_handler(
        _request: Request,
        error: InvalidAction,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"code": error.code, "detail": str(error)},
        )

    @app.exception_handler(RepositoryUnavailable)
    async def repository_unavailable_handler(
        _request: Request,
        _error: RepositoryUnavailable,
    ) -> JSONResponse:
        error_id = f"storage-{uuid4().hex}"
        logger.error("troubleshooting storage operation failed", extra={"error_id": error_id})
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "code": RepositoryUnavailable.code,
                "detail": "troubleshooting storage unavailable",
                "error_id": error_id,
            },
        )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/workbench")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "ok": True,
            "mode": module.mode,
            "write_execution_enabled": module.write_execution_enabled,
        }

    @app.get("/readyz", response_model=None)
    async def readyz() -> JSONResponse:
        readiness = diagnoses.readiness()
        if readiness.ready:
            return JSONResponse(
                content={
                    "ok": True,
                    "storage": storage_payload(readiness),
                }
            )
        error_id = f"storage-{uuid4().hex}"
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "ok": False,
                "code": RepositoryUnavailable.code,
                "detail": "troubleshooting storage unavailable",
                "error_id": error_id,
                "storage": storage_payload(readiness),
            },
        )

    @app.get("/v1/troubleshooting/capabilities")
    async def capabilities() -> dict[str, object]:
        readiness = diagnoses.readiness()
        return {
            "runtime_mode": module.mode,
            "storage": storage_payload(readiness),
            "knowledge_publication": {
                "mode": "transactional_outbox",
                "pending": readiness.pending_publications,
                "publisher_connected": False,
            },
            "write_execution_enabled": module.write_execution_enabled,
            "trusted_identity": False,
            "loopback_only": True,
        }

    @app.get("/workbench", response_class=HTMLResponse, include_in_schema=False)
    async def workbench() -> HTMLResponse:
        if not resolved_workbench.is_file():
            raise HTTPException(status_code=404, detail="workbench HTML not found")
        return HTMLResponse(resolved_workbench.read_text(encoding="utf-8"))

    @app.get("/v1/troubleshooting/diagnoses", response_model=list[Diagnosis])
    async def list_diagnoses(include_rehearsals: bool = False) -> list[Diagnosis]:
        return module.list(include_rehearsals=include_rehearsals)

    @app.post(
        "/v1/troubleshooting/diagnoses",
        response_model=Diagnosis,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_diagnosis(incident: IncidentContext) -> Diagnosis:
        return module.diagnose(incident)

    @app.post(
        "/v1/troubleshooting/rehearsals/903001",
        response_model=Diagnosis,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_rehearsal_903001(request: ActorRequest) -> Diagnosis:
        rehearsal_id = f"rehearsal-903001-{uuid4().hex[:12]}"
        incident = fixture_incident_903001().model_copy(
            update={
                "incident_id": rehearsal_id,
                "system": "CSDP-REHEARSAL",
                "trace_id": f"trace-{rehearsal_id}",
                "intake_source": "workbench_rehearsal",
                "raw_input": "synthetic fixture rehearsal for error_code=903001",
            }
        )
        return rehearsal_module.diagnose(
            incident,
            actor=request.actor,
            rehearsal=True,
        )

    @app.get("/v1/troubleshooting/diagnoses/{diagnosis_id}", response_model=Diagnosis)
    async def read_diagnosis(diagnosis_id: str) -> Diagnosis:
        return get_diagnosis(diagnosis_id)

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
        response_model=Diagnosis,
    )
    async def confirm_diagnosis(diagnosis_id: str, request: ActorRequest) -> Diagnosis:
        return module.apply(diagnosis_id, ConfirmDiagnosis(actor=request.actor))

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/transfer",
        response_model=Diagnosis,
    )
    async def transfer_diagnosis(
        diagnosis_id: str,
        request: TransferRequest,
    ) -> Diagnosis:
        return module.apply(diagnosis_id, TransferDiagnosis(request=request))

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{action_id}/approve",
        response_model=Diagnosis,
    )
    async def approve_action(
        diagnosis_id: str,
        action_id: str,
        request: ApprovalRequest,
    ) -> Diagnosis:
        return module.apply(
            diagnosis_id,
            ApproveAction(action_id=action_id, request=request),
        )

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{action_id}/record-outcome",
        response_model=Diagnosis,
    )
    async def record_action_outcome(
        diagnosis_id: str,
        action_id: str,
        request: ActionOutcomeRequest,
    ) -> Diagnosis:
        return module.apply(
            diagnosis_id,
            RecordActionOutcome(action_id=action_id, request=request),
        )

    @app.post(
        "/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
        response_model=Diagnosis,
    )
    async def close_diagnosis(
        diagnosis_id: str,
        request: CloseRequest,
    ) -> Diagnosis:
        return module.apply(diagnosis_id, CloseDiagnosis(request=request))

    @app.post("/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{action_id}/execute")
    async def execute_action(diagnosis_id: str, action_id: str) -> None:
        module.apply(diagnosis_id, ExecuteAction(action_id=action_id))

    return app


app = create_app()
