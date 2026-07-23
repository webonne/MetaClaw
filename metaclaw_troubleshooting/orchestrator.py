"""Deterministic-first orchestration for the troubleshooting MVP."""

from __future__ import annotations

from typing import Any

from .clock import utc_now
from .models import (
    ActionType,
    AnomalyCriterion,
    ApprovalStatus,
    Confidence,
    ContainsAndInRule,
    Diagnosis,
    DiagnosisStatus,
    EvidenceQuery,
    EvidenceResult,
    EvidenceStatus,
    ExecutionStatus,
    IncidentCompleteness,
    IncidentContext,
    MissingOrLteRule,
    MultipleGtRule,
    NumericGteRule,
    RatioOfSumGtRule,
    RouteMode,
    SopEntry,
    TimelineEvent,
)
from .ports import EvidenceCollector, SopRepository


def _number(observed: dict[str, Any], field: str) -> float | None:
    try:
        return float(observed[field])
    except (KeyError, TypeError, ValueError):
        return None


def _criterion_matches(criterion: AnomalyCriterion, observed: dict[str, Any]) -> bool:
    rule = criterion.rule
    if isinstance(rule, NumericGteRule):
        value = _number(observed, rule.field)
        return value is not None and value >= rule.threshold
    if isinstance(rule, MissingOrLteRule):
        value = _number(observed, rule.field)
        return not bool(observed.get(rule.presence_field)) or (value is not None and value <= rule.threshold)
    if isinstance(rule, RatioOfSumGtRule):
        numerator = _number(observed, rule.numerator_field)
        addend = _number(observed, rule.addend_field)
        if numerator is None or addend is None or numerator + addend <= 0:
            return False
        return numerator / (numerator + addend) > rule.threshold
    if isinstance(rule, MultipleGtRule):
        value = _number(observed, rule.field)
        baseline = _number(observed, rule.baseline_field)
        if value is None or baseline is None or baseline <= 0:
            return False
        return value > baseline * rule.multiplier
    if isinstance(rule, ContainsAndInRule):
        contains_value = str(observed.get(rule.contains_field, "")).lower()
        membership_value = str(observed.get(rule.membership_field, "")).lower()
        accepted = {value.lower() for value in rule.accepted_values}
        return rule.substring.lower() in contains_value and membership_value in accepted
    return False


class TroubleshootingOrchestrator:
    def __init__(
        self,
        sop_repository: SopRepository,
        evidence_collector: EvidenceCollector,
    ):
        self.sop_repository = sop_repository
        self.evidence_collector = evidence_collector

    @property
    def mode(self) -> str:
        return self.evidence_collector.mode

    def diagnose(self, incident: IncidentContext) -> Diagnosis:
        error_code = (incident.error_code or "").strip()
        if incident.completeness == IncidentCompleteness.SYMPTOM or not error_code:
            return self._fallback(incident)
        sop = self.sop_repository.get(incident.system, error_code)
        if sop is None:
            return self._fallback(incident)

        timeline = [
            self._event("故障上下文已接收", incident.intake_source),
            self._event(f"确定性路由命中 {sop.routing_key}", "orchestrator"),
        ]
        evidence, required_failures, collection_failures = self._collect_evidence(sop, incident)
        collection_note = f"自动取证 {len(evidence)} 项"
        if collection_failures:
            collection_note += f"，{len(collection_failures)} 项失败"
        timeline.append(self._event(collection_note, self.evidence_collector.mode))

        signals = self._evaluate_criteria(sop.anomaly_criteria, evidence)
        root_cause, summary, confidence, abstained = self._synthesize(sop, signals)
        if required_failures:
            root_cause = "自动取证不完整，当前不能确认根因。"
            summary = "观测工具不可用，已降级为 SOP 文本与人工取证指引。"
            confidence = Confidence.LOW
            abstained = True
        if not sop.is_operational:
            root_cause = "SOP 尚未审核，当前仅完成影子取证，不输出正式根因。"
            summary = "命中草案 SOP；结果仅用于离线比对，不能作为处置建议。"
            confidence = Confidence.LOW
            abstained = True

        status = DiagnosisStatus.NEEDS_INVESTIGATION if abstained else DiagnosisStatus.READY_FOR_HUMAN
        timeline.append(
            self._event(
                "证据或审核条件不足，转人工深查" if abstained else "诊断结论待人工确认",
                "orchestrator",
                status="current",
            )
        )

        actions = [] if abstained else self._safe_actions(sop)
        pending_writes = [
            action.model_copy(deep=True)
            for action in actions
            if action.action_type == ActionType.MANUAL_WRITE and action.approval_status == ApprovalStatus.PENDING
        ]
        route_to_team = (
            sop.owner_team if any(action.action_type == ActionType.HUMAN_CONTACT for action in actions) else None
        )
        warnings: list[str] = []
        if self.mode.startswith("fixture"):
            warnings.append("当前仅使用 fixture 证据；DQL 指标名与阈值尚未联调核实。")
        if collection_failures:
            failed = ", ".join(collection_failures)
            warnings.append(f"自动取证失败（{failed}）；请按 SOP 进行人工取证。")
        if not sop.is_operational:
            warnings.append("SOP 仍为草案，禁止越过影子模式或输出恢复动作。")

        return Diagnosis(
            diagnosis_id=f"diag-{incident.incident_id}",
            case_id=f"case-{incident.incident_id}",
            run_id=f"run-{incident.incident_id}-001",
            incident=incident,
            route_mode=RouteMode.DETERMINISTIC,
            status=status,
            summary=summary,
            root_cause=root_cause,
            confidence=confidence,
            abstained=abstained,
            sop_key=sop.routing_key,
            sop_title=sop.title,
            evidence=evidence,
            triggered_signals=signals,
            recommended_actions=actions,
            pending_writes=pending_writes,
            route_to_team=route_to_team,
            timeline=timeline,
            fixture_mode=self.mode.startswith("fixture"),
            write_execution_enabled=False,
            warnings=warnings,
        )

    def _collect_evidence(
        self,
        sop: SopEntry,
        incident: IncidentContext,
    ) -> tuple[list[EvidenceResult], list[str], list[str]]:
        evidence: list[EvidenceResult] = []
        required_failures: list[str] = []
        collection_failures: list[str] = []
        for query in sop.evidence_queries:
            try:
                result = self.evidence_collector.collect(query, incident)
            except Exception as error:  # external tool boundary must fail closed
                result = self._missing_evidence(query, type(error).__name__)
            evidence.append(result)
            if result.status == EvidenceStatus.MISSING:
                collection_failures.append(query.query_id)
                if query.required:
                    required_failures.append(query.query_id)
        return evidence, required_failures, collection_failures

    def _missing_evidence(self, query: EvidenceQuery, error_type: str) -> EvidenceResult:
        return EvidenceResult(
            query_id=query.query_id,
            namespace=query.namespace,
            query=query.query,
            status=EvidenceStatus.MISSING,
            summary="自动取证不可用，需人工执行该取证项",
            observed={"error_type": error_type},
            source=f"{self.mode}:unavailable",
            collected_at=utc_now(),
        )

    @staticmethod
    def _evaluate_criteria(criteria: list[AnomalyCriterion], evidence: list[EvidenceResult]) -> list[str]:
        by_id = {item.query_id: item for item in evidence}
        signals: list[str] = []
        for criterion in criteria:
            result = by_id.get(criterion.source_query_id)
            if (
                result is not None
                and result.status != EvidenceStatus.MISSING
                and _criterion_matches(criterion, result.observed)
            ):
                signals.append(criterion.signal)
        return signals

    @staticmethod
    def _synthesize(sop: SopEntry, signals: list[str]) -> tuple[str, str, Confidence, bool]:
        active = set(signals)
        for rule in sop.diagnosis_rules:
            if set(rule.required_signals).issubset(active):
                return (
                    rule.root_cause,
                    rule.summary,
                    rule.confidence,
                    rule.abstained,
                )
        return (
            "证据不足，暂不能确认根因。",
            "SOP 未提供与当前信号匹配的结论规则。",
            Confidence.LOW,
            True,
        )

    @staticmethod
    def _safe_actions(sop: SopEntry):
        actions = [action.model_copy(deep=True) for action in sop.actions]
        for action in actions:
            if action.action_type == ActionType.MANUAL_WRITE:
                action.requires_approval = True
                action.approval_status = ApprovalStatus.PENDING
                action.execution_status = ExecutionStatus.BLOCKED
        return actions

    def _fallback(self, incident: IncidentContext) -> Diagnosis:
        return Diagnosis(
            diagnosis_id=f"diag-{incident.incident_id}",
            case_id=f"case-{incident.incident_id}",
            run_id=f"run-{incident.incident_id}-001",
            incident=incident,
            route_mode=RouteMode.LLM_FALLBACK,
            status=DiagnosisStatus.NEEDS_INVESTIGATION,
            summary="未命中结构化 SOP，等待受控 LLM 分诊与人工确认。",
            root_cause="证据不足，暂不生成恢复动作。",
            confidence=Confidence.LOW,
            abstained=True,
            fixture_mode=self.mode.startswith("fixture"),
            write_execution_enabled=False,
            warnings=["MetaClaw LLM fallback 仅保留受控适配接口，当前 MVP 尚未接入。"],
            timeline=[
                self._event("故障上下文已接收", incident.intake_source),
                self._event("未命中 SOP，转受控 LLM 分诊", "orchestrator", status="current"),
            ],
        )

    @staticmethod
    def _event(event: str, actor: str, status: str = "done") -> TimelineEvent:
        return TimelineEvent(
            timestamp=utc_now(),
            event=event,
            actor=actor,
            status=status,
        )
