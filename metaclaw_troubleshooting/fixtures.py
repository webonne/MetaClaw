"""Explicit, non-production fixtures for the 903001 vertical slice."""

from __future__ import annotations

from .clock import utc_now
from .models import (
    ActionType,
    AnomalyCriterion,
    ApprovalStatus,
    Confidence,
    ContainsAndInRule,
    DiagnosisRule,
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
    RecommendedAction,
    SopEntry,
)


def fixture_incident_903001() -> IncidentContext:
    return IncidentContext(
        incident_id="inc-demo-903001",
        system="CSDP",
        service="csdp-wechat",
        error_code="903001",
        title="数据库访问异常",
        severity="P0",
        impact="所有客户·小程序不可用",
        trace_id="trace-demo-903001",
        sla_remaining="14:31",
        intake_source="alert_fixture",
        completeness=IncidentCompleteness.STRUCTURED,
        raw_input="fixture error_code=903001 mongo connection timeout",
    )


def fixture_sop_903001() -> SopEntry:
    return SopEntry(
        sop_id="mongo/conn-saturated",
        system="CSDP",
        error_code="903001",
        service="csdp-wechat",
        title="MongoDB 连接异常取证与处置",
        cause="MongoDB 不可达、连接数饱和，或客户端网络异常",
        category="依赖异常·中间件",
        owner_team="DBA 值班",
        status="draft",
        verified=False,
        evidence_queries=[
            EvidenceQuery(
                query_id="error-log",
                namespace="L::",
                purpose="确认 903001 发生并提取 trace_id",
                query=(
                    "L::csdp_wechat_log:(error_code,trace_id,error_msg,occur_time) "
                    "{service='csdp-wechat',error_code='903001'} [{{time_window}}] LIMIT 50"
                ),
            ),
            EvidenceQuery(
                query_id="mongo-metrics",
                namespace="M::",
                purpose="判断 MongoDB 是否可达、连接饱和或慢查询激增",
                query=(
                    "M::mongodb:(connections_current,connections_available,op_slow_count,uptime) "
                    "{host='<MONGODB_HOST>'} [{{time_window}}:1m]"
                ),
            ),
            EvidenceQuery(
                query_id="trace-db",
                namespace="T::",
                purpose="确认失败调用链是否定位到 MongoDB",
                query=(
                    "T::tracing:(service,resource,duration,status) {trace_id='{{trace_id}}'} ORDER BY start_time ASC"
                ),
            ),
            EvidenceQuery(
                query_id="impact",
                namespace="M::",
                purpose="量化 csdp-wechat 错误率与影响面",
                query=("M::apm_service:(error_rate,request_count) {service='csdp-wechat'} [{{time_window}}:1m]"),
                required=False,
            ),
        ],
        anomaly_criteria=[
            AnomalyCriterion(
                signal="log_hit",
                source_query_id="error-log",
                description="count(error_code=903001) >= 1",
                rule=NumericGteRule(field="count", threshold=1),
            ),
            AnomalyCriterion(
                signal="mongo_unreachable",
                source_query_id="mongo-metrics",
                description="无指标数据或 uptime 归零",
                rule=MissingOrLteRule(
                    presence_field="data_present",
                    field="uptime",
                    threshold=0,
                ),
            ),
            AnomalyCriterion(
                signal="conn_saturated",
                source_query_id="mongo-metrics",
                description="connections_current / (current + available) > 0.9",
                rule=RatioOfSumGtRule(
                    numerator_field="connections_current",
                    addend_field="connections_available",
                    threshold=0.9,
                ),
            ),
            AnomalyCriterion(
                signal="slow_spike",
                source_query_id="mongo-metrics",
                description="op_slow_count > baseline * 3",
                rule=MultipleGtRule(
                    field="op_slow_count",
                    baseline_field="op_slow_baseline",
                    multiplier=3,
                ),
            ),
            AnomalyCriterion(
                signal="trace_db_fail",
                source_query_id="trace-db",
                description="MongoDB span status=error 或超时",
                rule=ContainsAndInRule(
                    contains_field="peer",
                    substring="mongo",
                    membership_field="status",
                    accepted_values=("error", "timeout"),
                ),
            ),
        ],
        diagnosis_rules=[
            DiagnosisRule(
                rule_id="mongo-unreachable",
                required_signals=["mongo_unreachable"],
                root_cause="MongoDB 实例不可达，导致 csdp-wechat 数据库访问失败。",
                summary="已确认 903001 发生，MongoDB 可用性证据异常。",
                confidence=Confidence.HIGH,
            ),
            DiagnosisRule(
                rule_id="connection-saturated-with-trace",
                required_signals=["conn_saturated", "trace_db_fail"],
                root_cause="MongoDB 连接数饱和导致 csdp-wechat 数据库访问超时。",
                summary="错误日志、MongoDB 指标与调用链三类证据一致。",
                confidence=Confidence.HIGH,
            ),
            DiagnosisRule(
                rule_id="connection-saturated",
                required_signals=["conn_saturated"],
                root_cause="MongoDB 连接数饱和，但调用链定位证据不完整。",
                summary="指标证据支持连接饱和，待人工确认影响链路。",
                confidence=Confidence.MEDIUM,
            ),
            DiagnosisRule(
                rule_id="insufficient-evidence",
                root_cause="证据不足，暂不能确认 MongoDB 是根因。",
                summary="只确认了 903001 日志，数据库指标与调用链未支持相同结论。",
                confidence=Confidence.LOW,
                abstained=True,
            ),
        ],
        actions=[
            RecommendedAction(
                action_id="retain-evidence",
                action_type=ActionType.AUTO_READONLY,
                title="保留取证结果并持续观察",
                description="只读动作，不修改生产系统",
                execution_status=ExecutionStatus.COMPLETED,
            ),
            RecommendedAction(
                action_id="contact-dba",
                action_type=ActionType.HUMAN_CONTACT,
                title="联系 DBA 值班",
                description="携带错误码、trace_id 和 MongoDB 指标",
                execution_status=ExecutionStatus.PENDING,
            ),
            RecommendedAction(
                action_id="restart-mongodb",
                action_type=ActionType.MANUAL_WRITE,
                title="按中间件恢复方案重启 MongoDB 实例",
                description="生产写操作，仅记录人工批准；MVP 不提供执行器",
                requires_approval=True,
                approval_status=ApprovalStatus.PENDING,
                execution_status=ExecutionStatus.BLOCKED,
            ),
        ],
    )


class FixtureEvidenceCollector:
    mode = "fixture"

    def __init__(self, scenario: str = "saturated"):
        if scenario not in {"saturated", "log_only"}:
            raise ValueError(f"unknown evidence fixture scenario: {scenario}")
        self.scenario = scenario

    def collect(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult:
        if self.scenario == "saturated":
            return self._saturated(query, incident)
        return self._log_only(query, incident)

    def _saturated(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult:
        fixtures = {
            "error-log": (
                EvidenceStatus.ANOMALY,
                "903001 错误日志 47 条",
                {"count": 47, "trace_id": incident.trace_id or "trace-demo-903001"},
            ),
            "mongo-metrics": (
                EvidenceStatus.ANOMALY,
                "MongoDB 连接数 982/1000，慢查询激增",
                {
                    "data_present": True,
                    "uptime": 86400,
                    "connections_current": 982,
                    "connections_available": 18,
                    "op_slow_count": 84,
                    "op_slow_baseline": 20,
                },
            ),
            "trace-db": (
                EvidenceStatus.ANOMALY,
                "调用链在 MongoDB 访问跳超时",
                {"peer": "mongodb", "status": "error", "duration_ms": 5001},
            ),
            "impact": (
                EvidenceStatus.ANOMALY,
                "csdp-wechat 错误率升至 27%",
                {"error_rate": 0.27, "request_count": 1800},
            ),
        }
        return self._result(query, fixtures[query.query_id])

    def _log_only(self, query: EvidenceQuery, incident: IncidentContext) -> EvidenceResult:
        fixtures = {
            "error-log": (
                EvidenceStatus.ANOMALY,
                "903001 错误日志 3 条",
                {"count": 3, "trace_id": incident.trace_id or "trace-demo-903001"},
            ),
            "mongo-metrics": (
                EvidenceStatus.NORMAL,
                "MongoDB 指标正常",
                {
                    "data_present": True,
                    "uptime": 86400,
                    "connections_current": 320,
                    "connections_available": 680,
                    "op_slow_count": 20,
                    "op_slow_baseline": 18,
                },
            ),
            "trace-db": (
                EvidenceStatus.NORMAL,
                "未观察到 MongoDB 失败 span",
                {"peer": "mongodb", "status": "ok", "duration_ms": 42},
            ),
            "impact": (
                EvidenceStatus.NORMAL,
                "应用整体错误率未显著上升",
                {"error_rate": 0.01, "request_count": 1700},
            ),
        }
        return self._result(query, fixtures[query.query_id])

    def _result(
        self,
        query: EvidenceQuery,
        fixture: tuple[EvidenceStatus, str, dict[str, object]],
    ) -> EvidenceResult:
        status, summary, observed = fixture
        return EvidenceResult(
            query_id=query.query_id,
            namespace=query.namespace,
            query=query.query,
            status=status,
            summary=summary,
            observed=observed,
            source=f"fixture:{self.scenario}",
            collected_at=utc_now(),
        )
