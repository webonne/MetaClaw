"""Human-controlled lifecycle transitions for a troubleshooting diagnosis."""

from __future__ import annotations

from uuid import uuid4

from .clock import utc_now
from .models import (
    ActionOutcomeRecord,
    ActionOutcomeRequest,
    ActionOutcomeStatus,
    ApprovalRequest,
    ApprovalStatus,
    CloseRequest,
    ClosureOutcome,
    ClosureRecord,
    Diagnosis,
    DiagnosisStatus,
    KnowledgeCandidate,
    RecommendedAction,
    TimelineEvent,
    TransferContextSnapshot,
    TransferRecord,
    TransferRequest,
)


class WorkflowConflict(RuntimeError):
    """Raised when a requested mutation would skip a required lifecycle gate."""

    code = "workflow_conflict"


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class DiagnosisWorkflow:
    """Mutates one in-memory diagnosis while preserving human control gates."""

    @staticmethod
    def confirm(diagnosis: Diagnosis, actor: str) -> Diagnosis:
        if diagnosis.abstained:
            raise WorkflowConflict("abstained diagnosis requires new evidence before confirmation")
        if diagnosis.status != DiagnosisStatus.READY_FOR_HUMAN:
            raise WorkflowConflict("diagnosis is not waiting for confirmation")
        diagnosis.status = DiagnosisStatus.CONFIRMED
        DiagnosisWorkflow._append_event(diagnosis, "人工确认诊断结论", actor)
        return diagnosis

    @staticmethod
    def transfer(diagnosis: Diagnosis, request: TransferRequest) -> Diagnosis:
        DiagnosisWorkflow._require_confirmed(diagnosis)
        record = TransferRecord(
            transfer_id=_identifier("transfer"),
            target_team=request.target_team,
            note=request.note,
            actor=request.actor,
            transferred_at=utc_now(),
            context=TransferContextSnapshot(
                case_id=diagnosis.case_id,
                run_id=diagnosis.run_id,
                trace_id=diagnosis.incident.trace_id,
                evidence_ids=[item.query_id for item in diagnosis.evidence],
                root_cause=diagnosis.root_cause,
                confidence=diagnosis.confidence,
            ),
        )
        diagnosis.transfers.append(record)
        diagnosis.route_to_team = request.target_team
        diagnosis.status = DiagnosisStatus.TRANSFERRED
        DiagnosisWorkflow._append_event(
            diagnosis,
            f"结构化转派至 {request.target_team}（携带完整上下文）",
            request.actor,
        )
        return diagnosis

    @staticmethod
    def approve_action(
        diagnosis: Diagnosis,
        action: RecommendedAction,
        request: ApprovalRequest,
    ) -> Diagnosis:
        DiagnosisWorkflow._require_confirmed(diagnosis)
        if action.approval_status == ApprovalStatus.APPROVED:
            return diagnosis
        action.approval_status = ApprovalStatus.APPROVED
        diagnosis.pending_writes = [item for item in diagnosis.pending_writes if item.action_id != action.action_id]
        DiagnosisWorkflow._append_event(
            diagnosis,
            f"生产写操作已人工批准（{request.reason}，系统未执行）",
            request.actor,
        )
        return diagnosis

    @staticmethod
    def record_action_outcome(
        diagnosis: Diagnosis,
        action: RecommendedAction,
        request: ActionOutcomeRequest,
    ) -> Diagnosis:
        DiagnosisWorkflow._require_confirmed(diagnosis)
        if action.approval_status != ApprovalStatus.APPROVED:
            raise WorkflowConflict("manual write must be approved before recording an external outcome")
        if request.recovery_verified and request.outcome != ActionOutcomeStatus.SUCCEEDED:
            raise WorkflowConflict("only a succeeded external outcome can pass recovery verification")
        record = ActionOutcomeRecord(
            outcome_id=_identifier("outcome"),
            action_id=action.action_id,
            outcome=request.outcome,
            notes=request.notes,
            recovery_verified=request.recovery_verified,
            actor=request.actor,
            recorded_at=utc_now(),
        )
        diagnosis.action_outcomes.append(record)
        DiagnosisWorkflow._append_event(
            diagnosis,
            f"登记外部处置结果：{request.outcome.value}（MetaClaw 未执行）",
            request.actor,
        )
        if request.recovery_verified:
            DiagnosisWorkflow._append_event(diagnosis, "恢复验证通过", request.actor)
        return diagnosis

    @staticmethod
    def close(diagnosis: Diagnosis, request: CloseRequest) -> Diagnosis:
        DiagnosisWorkflow._require_confirmed(diagnosis)
        if request.outcome == ClosureOutcome.RECOVERED and not request.recovery_verified:
            raise WorkflowConflict("recovered closure requires recovery verification")
        if request.outcome != ClosureOutcome.RECOVERED and request.recovery_verified:
            raise WorkflowConflict("only recovered closure can carry recovery verification")
        if request.outcome == ClosureOutcome.RECOVERED and diagnosis.pending_writes:
            raise WorkflowConflict("pending manual writes must be resolved before recovered closure")
        DiagnosisWorkflow._require_outcomes_for_approved_writes(diagnosis, request)

        candidate: KnowledgeCandidate | None = None
        if request.create_knowledge_candidate:
            candidate = KnowledgeCandidate(
                candidate_id=_identifier("candidate"),
                source_diagnosis_id=diagnosis.diagnosis_id,
                source_case_id=diagnosis.case_id,
                source_run_id=diagnosis.run_id,
                system=diagnosis.incident.system,
                error_code=diagnosis.incident.error_code,
                sop_key=diagnosis.sop_key,
                root_cause=diagnosis.root_cause,
                evidence_ids=[item.query_id for item in diagnosis.evidence],
                recommended_actions=[item.model_copy(deep=True) for item in diagnosis.recommended_actions],
                action_outcomes=[item.model_copy(deep=True) for item in diagnosis.action_outcomes],
                resolution_summary=request.summary,
                feedback=request.sop_feedback,
                created_by=request.actor,
                created_at=utc_now(),
            )
            diagnosis.knowledge_candidates.append(candidate)

        diagnosis.closure = ClosureRecord(
            outcome=request.outcome,
            summary=request.summary,
            recovery_verified=request.recovery_verified,
            sop_feedback=request.sop_feedback,
            knowledge_candidate_id=candidate.candidate_id if candidate else None,
            actor=request.actor,
            closed_at=utc_now(),
        )
        diagnosis.status = DiagnosisStatus.CLOSED
        DiagnosisWorkflow._append_event(
            diagnosis,
            f"关闭归档：{request.outcome.value}（{request.summary}）",
            request.actor,
        )
        if candidate is not None:
            DiagnosisWorkflow._append_event(
                diagnosis,
                f"知识候选 {candidate.candidate_id} 已提交审核",
                request.actor,
            )
        return diagnosis

    @staticmethod
    def _require_confirmed(diagnosis: Diagnosis) -> None:
        if diagnosis.status not in {DiagnosisStatus.CONFIRMED, DiagnosisStatus.TRANSFERRED}:
            raise WorkflowConflict("diagnosis must be confirmed before this operation")

    @staticmethod
    def _require_outcomes_for_approved_writes(
        diagnosis: Diagnosis,
        request: CloseRequest,
    ) -> None:
        if request.outcome != ClosureOutcome.RECOVERED:
            return
        approved_ids = {
            action.action_id
            for action in diagnosis.recommended_actions
            if action.approval_status == ApprovalStatus.APPROVED
        }
        latest_by_action = {
            outcome.action_id: outcome for outcome in diagnosis.action_outcomes if outcome.action_id in approved_ids
        }
        incomplete = [
            action_id
            for action_id in approved_ids
            if action_id not in latest_by_action
            or latest_by_action[action_id].outcome != ActionOutcomeStatus.SUCCEEDED
            or not latest_by_action[action_id].recovery_verified
        ]
        if incomplete:
            raise WorkflowConflict(
                "approved manual writes require a succeeded external outcome and recovery verification before closure"
            )

    @staticmethod
    def _append_event(diagnosis: Diagnosis, event: str, actor: str) -> None:
        for item in diagnosis.timeline:
            if item.status == "current":
                item.status = "done"
        diagnosis.timeline.append(
            TimelineEvent(
                timestamp=utc_now(),
                event=event,
                actor=actor,
            )
        )
