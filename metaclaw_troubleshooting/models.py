"""Stable contracts for the intelligent troubleshooting vertical slice."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class RouteMode(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_FALLBACK = "llm_fallback"


class DiagnosisStatus(str, Enum):
    READY_FOR_HUMAN = "ready_for_human"
    NEEDS_INVESTIGATION = "needs_investigation"
    CONFIRMED = "confirmed"
    TRANSFERRED = "transferred"
    CLOSED = "closed"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionType(str, Enum):
    AUTO_READONLY = "auto_readonly"
    HUMAN_CONTACT = "human_contact"
    MANUAL_UNKNOWN = "manual_unknown"
    MANUAL_WRITE = "manual_write"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    PENDING = "pending"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class ActionOutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClosureOutcome(str, Enum):
    RECOVERED = "recovered"
    FALSE_POSITIVE = "false_positive"
    TRANSFERRED_OUT = "transferred_out"
    UNRESOLVED = "unresolved"


class KnowledgeCandidateStatus(str, Enum):
    CANDIDATE = "candidate"


class KnowledgePublicationStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class EvidenceStatus(str, Enum):
    NORMAL = "normal"
    ANOMALY = "anomaly"
    MISSING = "missing"


class IncidentCompleteness(str, Enum):
    STRUCTURED = "structured"
    LOG = "log"
    SYMPTOM = "symptom"


class IncidentContext(BaseModel):
    incident_id: str = Field(min_length=1)
    system: str = Field(min_length=1)
    service: str = Field(min_length=1)
    error_code: str | None = None
    title: str = ""
    severity: str = "P2"
    impact: str = "待确认"
    trace_id: str | None = None
    occurred_at: str | None = None
    sla_remaining: str | None = None
    intake_source: str = "manual"
    completeness: IncidentCompleteness = IncidentCompleteness.STRUCTURED
    raw_input: str | None = None


class EvidenceQuery(BaseModel):
    query_id: str
    namespace: str
    purpose: str
    query: str
    required: bool = True
    verification_status: str = "unverified"


class NumericGteRule(BaseModel):
    kind: Literal["numeric_gte"] = "numeric_gte"
    field: str
    threshold: float


class MissingOrLteRule(BaseModel):
    kind: Literal["missing_or_lte"] = "missing_or_lte"
    presence_field: str
    field: str
    threshold: float


class RatioOfSumGtRule(BaseModel):
    kind: Literal["ratio_of_sum_gt"] = "ratio_of_sum_gt"
    numerator_field: str
    addend_field: str
    threshold: float


class MultipleGtRule(BaseModel):
    kind: Literal["multiple_gt"] = "multiple_gt"
    field: str
    baseline_field: str
    multiplier: float


class ContainsAndInRule(BaseModel):
    kind: Literal["contains_and_in"] = "contains_and_in"
    contains_field: str
    substring: str
    membership_field: str
    accepted_values: tuple[str, ...]


CriterionRule = Annotated[
    NumericGteRule | MissingOrLteRule | RatioOfSumGtRule | MultipleGtRule | ContainsAndInRule,
    Field(discriminator="kind"),
]


class AnomalyCriterion(BaseModel):
    signal: str
    source_query_id: str
    description: str
    rule: CriterionRule


class DiagnosisRule(BaseModel):
    rule_id: str
    required_signals: list[str] = Field(default_factory=list)
    root_cause: str
    summary: str
    confidence: Confidence
    abstained: bool = False


class RecommendedAction(BaseModel):
    action_id: str
    action_type: ActionType
    title: str
    description: str = ""
    requires_approval: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    execution_status: ExecutionStatus = ExecutionStatus.PENDING


class SopEntry(BaseModel):
    sop_id: str
    system: str
    error_code: str
    service: str
    title: str
    cause: str
    category: str
    owner_team: str | None = None
    status: str = "candidate"
    verified: bool = False
    evidence_queries: list[EvidenceQuery] = Field(default_factory=list)
    anomaly_criteria: list[AnomalyCriterion] = Field(default_factory=list)
    diagnosis_rules: list[DiagnosisRule] = Field(default_factory=list)
    actions: list[RecommendedAction] = Field(default_factory=list)

    @property
    def routing_key(self) -> str:
        return f"{self.system.strip().lower()}:{self.error_code.strip()}"

    @property
    def is_operational(self) -> bool:
        return self.verified and self.status == "approved"


class EvidenceResult(BaseModel):
    query_id: str
    namespace: str
    query: str
    status: EvidenceStatus
    summary: str
    observed: dict[str, Any] = Field(default_factory=dict)
    source: str
    collected_at: str


class TimelineEvent(BaseModel):
    timestamp: str
    event: str
    actor: str
    status: str = "done"


class TransferContextSnapshot(BaseModel):
    case_id: str
    run_id: str
    trace_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    root_cause: str
    confidence: Confidence


class TransferRecord(BaseModel):
    transfer_id: str
    target_team: str
    note: str
    actor: str
    transferred_at: str
    context: TransferContextSnapshot


class ActionOutcomeRecord(BaseModel):
    outcome_id: str
    action_id: str
    outcome: ActionOutcomeStatus
    notes: str
    recovery_verified: bool = False
    actor: str
    recorded_at: str


class ClosureRecord(BaseModel):
    outcome: ClosureOutcome
    summary: str
    recovery_verified: bool = False
    sop_feedback: str | None = None
    knowledge_candidate_id: str | None = None
    actor: str
    closed_at: str


class KnowledgeCandidate(BaseModel):
    candidate_id: str
    status: KnowledgeCandidateStatus = KnowledgeCandidateStatus.CANDIDATE
    source_diagnosis_id: str
    source_case_id: str
    source_run_id: str
    system: str
    error_code: str | None = None
    sop_key: str | None = None
    root_cause: str
    evidence_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    action_outcomes: list[ActionOutcomeRecord] = Field(default_factory=list)
    resolution_summary: str
    feedback: str | None = None
    created_by: str
    created_at: str


class KnowledgePublication(BaseModel):
    """Versioned outbox envelope owned by the troubleshooting module."""

    publication_id: str
    contract_version: str = "knowledge-candidate.v1"
    diagnosis_id: str
    candidate_id: str
    payload: KnowledgeCandidate
    status: KnowledgePublicationStatus = KnowledgePublicationStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    claimed_by: str | None = None
    lease_expires_at: str | None = None
    created_at: str
    updated_at: str


class Diagnosis(BaseModel):
    diagnosis_id: str
    contract_version: str = "1.3"
    case_id: str
    run_id: str
    incident: IncidentContext
    route_mode: RouteMode
    status: DiagnosisStatus
    summary: str
    root_cause: str
    confidence: Confidence
    abstained: bool
    sop_key: str | None = None
    sop_title: str | None = None
    evidence: list[EvidenceResult] = Field(default_factory=list)
    triggered_signals: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    pending_writes: list[RecommendedAction] = Field(default_factory=list)
    route_to_team: str | None = None
    transfers: list[TransferRecord] = Field(default_factory=list)
    action_outcomes: list[ActionOutcomeRecord] = Field(default_factory=list)
    closure: ClosureRecord | None = None
    knowledge_candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    rehearsal: bool = False
    fixture_mode: bool = True
    write_execution_enabled: bool = False
    warnings: list[str] = Field(default_factory=list)


class ActorRequest(BaseModel):
    actor: str = Field(min_length=1)


class ApprovalRequest(ActorRequest):
    reason: str = Field(min_length=1)


class TransferRequest(ActorRequest):
    target_team: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ActionOutcomeRequest(ActorRequest):
    outcome: ActionOutcomeStatus
    notes: str = Field(min_length=1)
    recovery_verified: bool = False


class CloseRequest(ActorRequest):
    outcome: ClosureOutcome
    summary: str = Field(min_length=1)
    recovery_verified: bool = False
    sop_feedback: str | None = None
    create_knowledge_candidate: bool = False
