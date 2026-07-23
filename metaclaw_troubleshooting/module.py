"""Application Interface for diagnosis creation, queries, and lifecycle commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .clock import utc_now
from .models import (
    ActionOutcomeRequest,
    ActionType,
    ApprovalRequest,
    CloseRequest,
    Diagnosis,
    IncidentContext,
    RecommendedAction,
    TimelineEvent,
    TransferRequest,
)
from .orchestrator import TroubleshootingOrchestrator
from .ports import DiagnosisRepository
from .workflow import DiagnosisWorkflow


class TroubleshootingModuleError(RuntimeError):
    """Base error returned through the Module Interface."""

    code = "troubleshooting_error"


class TroubleshootingNotFound(TroubleshootingModuleError):
    """Base error for missing diagnoses or actions."""

    code = "troubleshooting_not_found"


class DiagnosisNotFound(TroubleshootingNotFound):
    code = "diagnosis_not_found"


class ActionNotFound(TroubleshootingNotFound):
    code = "action_not_found"


class InvalidAction(TroubleshootingModuleError):
    code = "invalid_action"


class ProductionWriteDisabled(TroubleshootingModuleError):
    code = "production_write_disabled"


@dataclass(frozen=True)
class ConfirmDiagnosis:
    actor: str


@dataclass(frozen=True)
class TransferDiagnosis:
    request: TransferRequest


@dataclass(frozen=True)
class ApproveAction:
    action_id: str
    request: ApprovalRequest


@dataclass(frozen=True)
class RecordActionOutcome:
    action_id: str
    request: ActionOutcomeRequest


@dataclass(frozen=True)
class CloseDiagnosis:
    request: CloseRequest


@dataclass(frozen=True)
class ExecuteAction:
    action_id: str


DiagnosisCommand: TypeAlias = (
    ConfirmDiagnosis | TransferDiagnosis | ApproveAction | RecordActionOutcome | CloseDiagnosis | ExecuteAction
)


class TroubleshootingModule:
    """Deep Module that owns diagnosis state and human-controlled transitions."""

    def __init__(
        self,
        orchestrator: TroubleshootingOrchestrator,
        diagnoses: DiagnosisRepository,
        workflow: DiagnosisWorkflow | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._diagnoses = diagnoses
        self._workflow = workflow or DiagnosisWorkflow()

    @property
    def mode(self) -> str:
        return self._orchestrator.mode

    @property
    def write_execution_enabled(self) -> bool:
        return False

    def diagnose(
        self,
        incident: IncidentContext,
        *,
        actor: str | None = None,
        rehearsal: bool = False,
    ) -> Diagnosis:
        diagnosis = self._orchestrator.diagnose(incident)
        if rehearsal:
            diagnosis.rehearsal = True
            diagnosis.warnings.insert(
                0,
                "合成演练：全部证据与处置结果均为 fixture，不代表生产结论，不连接生产写执行器。",
            )
            error_code = incident.error_code or "未知错误码"
            diagnosis.timeline.insert(
                0,
                TimelineEvent(
                    timestamp=utc_now(),
                    event=f"{error_code} 主流程合成演练已发起",
                    actor=actor or incident.intake_source,
                ),
            )
        return self._diagnoses.add(diagnosis)

    def apply(self, diagnosis_id: str, command: DiagnosisCommand) -> Diagnosis:
        updated = self._diagnoses.update(
            diagnosis_id,
            lambda diagnosis: self._apply_command(diagnosis, command),
        )
        if updated is None:
            raise DiagnosisNotFound("diagnosis not found")
        return updated

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        return self._diagnoses.get(diagnosis_id)

    def list(self, *, include_rehearsals: bool = False) -> list[Diagnosis]:
        return self._diagnoses.list(include_rehearsals=include_rehearsals)

    def _apply_command(self, diagnosis: Diagnosis, command: DiagnosisCommand) -> Diagnosis:
        if isinstance(command, ConfirmDiagnosis):
            return self._workflow.confirm(diagnosis, command.actor)
        elif isinstance(command, TransferDiagnosis):
            return self._workflow.transfer(diagnosis, command.request)
        elif isinstance(command, ApproveAction):
            action = self._require_action(diagnosis, command.action_id)
            if action.action_type != ActionType.MANUAL_WRITE or not action.requires_approval:
                raise InvalidAction("action does not require approval")
            return self._workflow.approve_action(diagnosis, action, command.request)
        elif isinstance(command, RecordActionOutcome):
            action = self._require_action(diagnosis, command.action_id)
            if action.action_type != ActionType.MANUAL_WRITE:
                raise InvalidAction("only manual writes have external outcomes")
            return self._workflow.record_action_outcome(diagnosis, action, command.request)
        elif isinstance(command, CloseDiagnosis):
            return self._workflow.close(diagnosis, command.request)
        elif isinstance(command, ExecuteAction):
            self._require_action(diagnosis, command.action_id)
            raise ProductionWriteDisabled("production write executor is not connected in this MVP")
        else:  # pragma: no cover - callers are type checked against DiagnosisCommand
            raise TypeError(f"unsupported diagnosis command: {type(command).__name__}")

    @staticmethod
    def _require_action(diagnosis: Diagnosis, action_id: str) -> RecommendedAction:
        action = next(
            (item for item in diagnosis.recommended_actions if item.action_id == action_id),
            None,
        )
        if action is None:
            raise ActionNotFound("action not found")
        return action
