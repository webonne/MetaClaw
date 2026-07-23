import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient

from metaclaw_troubleshooting.__main__ import main as troubleshooting_main
from metaclaw_troubleshooting.api import create_app
from metaclaw_troubleshooting.factory import (
    build_fixture_orchestrator,
    fixture_incident_903001,
)
from metaclaw_troubleshooting.fixtures import (
    FixtureEvidenceCollector,
    fixture_sop_903001,
)
from metaclaw_troubleshooting.models import (
    ActionOutcomeRequest,
    ActionType,
    ApprovalRequest,
    ApprovalStatus,
    CloseRequest,
    ClosureOutcome,
    Confidence,
    DiagnosisStatus,
    EvidenceStatus,
    ExecutionStatus,
    IncidentCompleteness,
    IncidentContext,
    RouteMode,
    TransferRequest,
)
from metaclaw_troubleshooting.module import (
    ApproveAction,
    CloseDiagnosis,
    ConfirmDiagnosis,
    ExecuteAction,
    ProductionWriteDisabled,
    RecordActionOutcome,
    TransferDiagnosis,
    TroubleshootingModule,
)
from metaclaw_troubleshooting.orchestrator import TroubleshootingOrchestrator
from metaclaw_troubleshooting.repository import (
    InMemoryDiagnosisRepository,
    InMemorySopRepository,
    SopKeyCollisionError,
)


def build_approved_fixture_orchestrator(
    scenario: str = "saturated",
) -> TroubleshootingOrchestrator:
    sop = fixture_sop_903001()
    sop.status = "approved"
    sop.verified = True
    return TroubleshootingOrchestrator(
        sop_repository=InMemorySopRepository([sop]),
        evidence_collector=FixtureEvidenceCollector(scenario=scenario),
    )


class TimeoutEvidenceCollector:
    mode = "fixture-timeout"

    def collect(self, query, incident):
        raise TimeoutError("simulated collector timeout")


class CoordinatedGetDiagnosisRepository(InMemoryDiagnosisRepository):
    """Forces legacy get/mutate/save callers to read the same revision."""

    def __init__(self):
        super().__init__()
        self.coordinate_reads = False
        self._read_barrier = Barrier(2)

    def get(self, diagnosis_id: str):
        diagnosis = super().get(diagnosis_id)
        if self.coordinate_reads:
            self._read_barrier.wait(timeout=2)
        return diagnosis


class TroubleshootingOrchestratorTests(unittest.TestCase):
    def test_unverified_sop_stays_shadow_and_exposes_no_actions(self):
        orchestrator = build_fixture_orchestrator()

        diagnosis = orchestrator.diagnose(fixture_incident_903001())

        self.assertEqual(diagnosis.route_mode, RouteMode.DETERMINISTIC)
        self.assertEqual(diagnosis.status, DiagnosisStatus.NEEDS_INVESTIGATION)
        self.assertEqual(diagnosis.confidence, Confidence.LOW)
        self.assertTrue(diagnosis.abstained)
        self.assertEqual(diagnosis.recommended_actions, [])
        self.assertEqual(diagnosis.pending_writes, [])
        self.assertIsNone(diagnosis.route_to_team)
        self.assertIn("SOP 尚未审核", diagnosis.root_cause)
        self.assertTrue(any("草案" in warning for warning in diagnosis.warnings))

    def test_approved_903001_uses_rules_and_blocks_write_execution(self):
        orchestrator = build_approved_fixture_orchestrator()

        diagnosis = orchestrator.diagnose(fixture_incident_903001())

        self.assertEqual(diagnosis.route_mode, RouteMode.DETERMINISTIC)
        self.assertEqual(diagnosis.status, DiagnosisStatus.READY_FOR_HUMAN)
        self.assertEqual(diagnosis.confidence, Confidence.HIGH)
        self.assertFalse(diagnosis.abstained)
        self.assertEqual(diagnosis.sop_key, "csdp:903001")
        self.assertEqual(len(diagnosis.evidence), 4)
        self.assertEqual(
            set(diagnosis.triggered_signals),
            {"log_hit", "conn_saturated", "slow_spike", "trace_db_fail"},
        )
        self.assertIn("MongoDB 连接数饱和", diagnosis.root_cause)

        write_actions = [
            action for action in diagnosis.recommended_actions if action.action_type == ActionType.MANUAL_WRITE
        ]
        self.assertEqual(len(write_actions), 1)
        self.assertTrue(write_actions[0].requires_approval)
        self.assertEqual(write_actions[0].approval_status, ApprovalStatus.PENDING)
        self.assertEqual(write_actions[0].execution_status, ExecutionStatus.BLOCKED)
        self.assertEqual(
            [action.action_id for action in diagnosis.pending_writes],
            ["restart-mongodb"],
        )
        self.assertEqual(diagnosis.route_to_team, "DBA 值班")
        self.assertFalse(diagnosis.write_execution_enabled)

    def test_903001_abstains_when_only_the_error_log_is_abnormal(self):
        orchestrator = build_approved_fixture_orchestrator(scenario="log_only")

        diagnosis = orchestrator.diagnose(fixture_incident_903001())

        self.assertEqual(diagnosis.triggered_signals, ["log_hit"])
        self.assertEqual(diagnosis.confidence, Confidence.LOW)
        self.assertEqual(diagnosis.status, DiagnosisStatus.NEEDS_INVESTIGATION)
        self.assertTrue(diagnosis.abstained)
        self.assertIn("证据不足", diagnosis.root_cause)
        self.assertEqual(diagnosis.recommended_actions, [])

    def test_collector_timeout_degrades_without_raising_or_recommending_actions(self):
        sop = fixture_sop_903001()
        sop.status = "approved"
        sop.verified = True
        orchestrator = TroubleshootingOrchestrator(
            sop_repository=InMemorySopRepository([sop]),
            evidence_collector=TimeoutEvidenceCollector(),
        )

        diagnosis = orchestrator.diagnose(fixture_incident_903001())

        self.assertTrue(diagnosis.abstained)
        self.assertEqual(diagnosis.confidence, Confidence.LOW)
        self.assertEqual(diagnosis.status, DiagnosisStatus.NEEDS_INVESTIGATION)
        self.assertEqual(len(diagnosis.evidence), 4)
        self.assertTrue(all(item.status == EvidenceStatus.MISSING for item in diagnosis.evidence))
        self.assertEqual(diagnosis.recommended_actions, [])
        self.assertTrue(any("人工取证" in warning for warning in diagnosis.warnings))

    def test_sop_criterion_threshold_drives_signal_evaluation(self):
        sop = fixture_sop_903001()
        sop.status = "approved"
        sop.verified = True
        criterion = next(item for item in sop.anomaly_criteria if item.signal == "conn_saturated")
        criterion.rule.threshold = 0.99
        orchestrator = TroubleshootingOrchestrator(
            sop_repository=InMemorySopRepository([sop]),
            evidence_collector=FixtureEvidenceCollector(),
        )

        diagnosis = orchestrator.diagnose(fixture_incident_903001())

        self.assertNotIn("conn_saturated", diagnosis.triggered_signals)
        self.assertTrue(diagnosis.abstained)
        self.assertEqual(diagnosis.recommended_actions, [])

    def test_symptom_completeness_does_not_enter_deterministic_route(self):
        incident = fixture_incident_903001().model_copy(update={"completeness": IncidentCompleteness.SYMPTOM})

        diagnosis = build_approved_fixture_orchestrator().diagnose(incident)

        self.assertEqual(diagnosis.route_mode, RouteMode.LLM_FALLBACK)
        self.assertEqual(diagnosis.evidence, [])
        self.assertTrue(diagnosis.abstained)

    def test_unknown_error_code_routes_to_llm_fallback_without_actions(self):
        orchestrator = build_fixture_orchestrator()
        incident = IncidentContext(
            incident_id="inc-unknown",
            system="CSDP",
            service="csdp-wechat",
            error_code="999999",
            title="未知异常",
            severity="P2",
            completeness=IncidentCompleteness.LOG,
            intake_source="manual",
            raw_input="error_code=999999 request failed",
        )

        diagnosis = orchestrator.diagnose(incident)

        self.assertEqual(diagnosis.route_mode, RouteMode.LLM_FALLBACK)
        self.assertEqual(diagnosis.status, DiagnosisStatus.NEEDS_INVESTIGATION)
        self.assertTrue(diagnosis.abstained)
        self.assertEqual(diagnosis.evidence, [])
        self.assertEqual(diagnosis.recommended_actions, [])
        self.assertIn("MetaClaw", diagnosis.warnings[0])

    def test_sop_repository_rejects_duplicate_d1_routing_keys(self):
        original = fixture_sop_903001()
        duplicate = original.model_copy(update={"sop_id": "duplicate"})

        with self.assertRaisesRegex(SopKeyCollisionError, "csdp,903001"):
            InMemorySopRepository([original, duplicate])


class TroubleshootingModuleTests(unittest.TestCase):
    def test_module_owns_diagnose_query_and_confirm_with_repository_isolation(self):
        repository = InMemoryDiagnosisRepository()
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=repository,
        )

        created = module.diagnose(fixture_incident_903001())
        fetched = module.get(created.diagnosis_id)

        self.assertIsNotNone(fetched)
        self.assertIsNot(created, fetched)
        fetched.status = DiagnosisStatus.CLOSED
        self.assertEqual(module.get(created.diagnosis_id).status, DiagnosisStatus.READY_FOR_HUMAN)

        confirmed = module.apply(
            created.diagnosis_id,
            ConfirmDiagnosis(actor="on-call"),
        )

        self.assertEqual(confirmed.status, DiagnosisStatus.CONFIRMED)
        self.assertEqual(module.get(created.diagnosis_id).status, DiagnosisStatus.CONFIRMED)
        self.assertEqual(module.list()[0].timeline[-1].actor, "on-call")

    def test_module_keeps_composite_idempotency_out_of_http_adapter(self):
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=InMemoryDiagnosisRepository(),
        )
        base = fixture_incident_903001().model_copy(update={"occurred_at": "2026-07-23T04:22:30+00:00"})

        first = module.diagnose(base.model_copy(update={"incident_id": "inc-first"}))
        duplicate = module.diagnose(base.model_copy(update={"incident_id": "inc-duplicate"}))

        self.assertEqual(duplicate.diagnosis_id, first.diagnosis_id)
        self.assertEqual(len(module.list()), 1)

    def test_repository_keeps_ingestion_bucket_stable_when_occurred_at_is_missing(self):
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=InMemoryDiagnosisRepository(),
        )
        first_incident = fixture_incident_903001().model_copy(update={"incident_id": "inc-first-without-occurred-at"})

        with patch("metaclaw_troubleshooting.repository.datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 23, 4, 22, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            first = module.diagnose(first_incident)

        with patch("metaclaw_troubleshooting.repository.datetime") as clock:
            clock.now.return_value = datetime(2026, 7, 23, 4, 28, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            module.apply(first.diagnosis_id, ConfirmDiagnosis(actor="on-call"))
            second = module.diagnose(
                first_incident.model_copy(update={"incident_id": "inc-second-without-occurred-at"})
            )

        self.assertNotEqual(second.diagnosis_id, first.diagnosis_id)
        self.assertEqual(len(module.list()), 2)

    def test_module_applies_concurrent_commands_without_losing_audit_state(self):
        repository = CoordinatedGetDiagnosisRepository()
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=repository,
        )
        diagnosis = module.diagnose(fixture_incident_903001())
        module.apply(diagnosis.diagnosis_id, ConfirmDiagnosis(actor="on-call"))
        write_action = next(
            action for action in diagnosis.recommended_actions if action.action_type == ActionType.MANUAL_WRITE
        )
        commands = [
            TransferDiagnosis(
                request=TransferRequest(
                    actor="on-call",
                    target_team="DBA 值班",
                    note="并发转派",
                )
            ),
            ApproveAction(
                action_id=write_action.action_id,
                request=ApprovalRequest(actor="dba", reason="并发审批"),
            ),
        ]

        repository.coordinate_reads = True
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(module.apply, diagnosis.diagnosis_id, command) for command in commands]
            for future in futures:
                future.result(timeout=2)
        repository.coordinate_reads = False

        stored = module.get(diagnosis.diagnosis_id)
        stored_write_action = next(
            action for action in stored.recommended_actions if action.action_id == write_action.action_id
        )
        self.assertEqual(stored.status, DiagnosisStatus.TRANSFERRED)
        self.assertEqual(len(stored.transfers), 1)
        self.assertEqual(stored_write_action.approval_status, ApprovalStatus.APPROVED)

    def test_module_applies_full_human_flow_and_keeps_write_executor_disconnected(self):
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=InMemoryDiagnosisRepository(),
        )
        diagnosis = module.diagnose(fixture_incident_903001())
        diagnosis_id = diagnosis.diagnosis_id
        write_action = next(
            action for action in diagnosis.recommended_actions if action.action_type == ActionType.MANUAL_WRITE
        )

        module.apply(diagnosis_id, ConfirmDiagnosis(actor="on-call"))
        module.apply(
            diagnosis_id,
            TransferDiagnosis(
                request=TransferRequest(
                    actor="on-call",
                    target_team="DBA 值班",
                    note="携带上下文转派",
                )
            ),
        )
        module.apply(
            diagnosis_id,
            ApproveAction(
                action_id=write_action.action_id,
                request=ApprovalRequest(actor="dba", reason="维护窗口已确认"),
            ),
        )
        module.apply(
            diagnosis_id,
            RecordActionOutcome(
                action_id=write_action.action_id,
                request=ActionOutcomeRequest(
                    actor="dba",
                    outcome="succeeded",
                    notes="外部系统已执行",
                    recovery_verified=True,
                ),
            ),
        )
        closed = module.apply(
            diagnosis_id,
            CloseDiagnosis(
                request=CloseRequest(
                    actor="on-call",
                    outcome=ClosureOutcome.RECOVERED,
                    summary="业务探测恢复",
                    recovery_verified=True,
                    create_knowledge_candidate=True,
                )
            ),
        )

        self.assertEqual(closed.status, DiagnosisStatus.CLOSED)
        self.assertEqual(len(closed.knowledge_candidates), 1)
        with self.assertRaisesRegex(ProductionWriteDisabled, "not connected"):
            module.apply(diagnosis_id, ExecuteAction(action_id=write_action.action_id))

    def test_module_marks_rehearsal_before_storage_and_excludes_it_from_default_list(self):
        repository = InMemoryDiagnosisRepository()
        module = TroubleshootingModule(
            orchestrator=build_approved_fixture_orchestrator(),
            diagnoses=repository,
        )
        incident = fixture_incident_903001().model_copy(
            update={
                "incident_id": "rehearsal-903001-module",
                "system": "CSDP",
                "intake_source": "workbench_rehearsal",
            }
        )

        diagnosis = module.diagnose(
            incident,
            actor="demo-on-call",
            rehearsal=True,
        )

        self.assertTrue(diagnosis.rehearsal)
        self.assertIn("合成演练", diagnosis.warnings[0])
        self.assertEqual(diagnosis.timeline[0].actor, "demo-on-call")
        self.assertEqual(module.list(), [])
        self.assertEqual(len(module.list(include_rehearsals=True)), 1)


class TroubleshootingEntrypointTests(unittest.TestCase):
    @patch("metaclaw_troubleshooting.__main__._serve")
    def test_entrypoint_accepts_loopback_binding(self, run_server):
        troubleshooting_main(["--host", "::1", "--port", "18083"])

        run_server.assert_called_once_with(
            "::1",
            18083,
            Path.home() / ".metaclaw" / "troubleshooting.db",
        )

    @patch("metaclaw_troubleshooting.__main__._serve")
    def test_entrypoint_accepts_explicit_database_path(self, run_server):
        database_path = Path("/tmp/metaclaw-troubleshooting-test.db")

        troubleshooting_main(["--database", str(database_path)])

        run_server.assert_called_once_with("127.0.0.1", 18080, database_path)

    @patch("metaclaw_troubleshooting.__main__._serve")
    def test_entrypoint_rejects_non_loopback_binding_before_auth_exists(self, run_server):
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaisesRegex(SystemExit, "2"):
            troubleshooting_main(["--host", "0.0.0.0"])

        run_server.assert_not_called()
        self.assertIn("non-loopback binding is disabled", stderr.getvalue())


class TroubleshootingApiTests(unittest.TestCase):
    def setUp(self):
        app = create_app(orchestrator=build_fixture_orchestrator(), seed_demo=True)
        self.client = TestClient(app)

    def test_health_and_seed_diagnosis_expose_fixture_mode(self):
        health = self.client.get("/healthz")
        diagnoses = self.client.get("/v1/troubleshooting/diagnoses")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.json(),
            {
                "ok": True,
                "mode": "fixture",
                "write_execution_enabled": False,
            },
        )
        self.assertEqual(diagnoses.status_code, 200)
        self.assertEqual(len(diagnoses.json()), 1)
        seeded = diagnoses.json()[0]
        self.assertEqual(seeded["incident"]["error_code"], "903001")
        self.assertEqual(seeded["incident"]["completeness"], "structured")
        self.assertTrue(seeded["abstained"])
        self.assertEqual(seeded["recommended_actions"], [])

    def test_create_get_and_confirm_diagnosis(self):
        client = TestClient(create_app(orchestrator=build_approved_fixture_orchestrator(), seed_demo=False))
        payload = {
            "incident_id": "inc-api-903001",
            "system": "csdp",
            "service": "csdp-wechat",
            "error_code": "903001",
            "title": "数据库访问异常",
            "severity": "P0",
            "completeness": "log",
            "intake_source": "manual",
            "raw_input": "error_code=903001 mongo timeout",
        }

        created = client.post("/v1/troubleshooting/diagnoses", json=payload)

        self.assertEqual(created.status_code, 201)
        diagnosis_id = created.json()["diagnosis_id"]
        fetched = client.get(f"/v1/troubleshooting/diagnoses/{diagnosis_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["route_mode"], "deterministic")

        confirmed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "on-call"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "confirmed")
        self.assertEqual(confirmed.json()["timeline"][-1]["actor"], "on-call")

    def test_abstained_diagnosis_cannot_be_confirmed_without_new_evidence(self):
        diagnosis_id = self.client.get("/v1/troubleshooting/diagnoses").json()[0]["diagnosis_id"]

        response = self.client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "on-call"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "workflow_conflict")
        self.assertIn("requires new evidence", response.json()["detail"])

    def test_domain_errors_expose_stable_machine_codes(self):
        client = TestClient(
            create_app(
                orchestrator=build_approved_fixture_orchestrator(),
                seed_demo=True,
            )
        )

        missing_diagnosis = client.get("/v1/troubleshooting/diagnoses/does-not-exist")
        self.assertEqual(missing_diagnosis.status_code, 404)
        self.assertEqual(missing_diagnosis.json()["code"], "diagnosis_not_found")

        diagnosis = client.get("/v1/troubleshooting/diagnoses").json()[0]
        diagnosis_id = diagnosis["diagnosis_id"]
        client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "on-call"},
        )
        invalid_action = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/retain-evidence/approve",
            json={"actor": "on-call", "reason": "不应审批只读动作"},
        )
        self.assertEqual(invalid_action.status_code, 400)
        self.assertEqual(invalid_action.json()["code"], "invalid_action")

        missing_action = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/does-not-exist/approve",
            json={"actor": "on-call", "reason": "不存在"},
        )
        self.assertEqual(missing_action.status_code, 404)
        self.assertEqual(missing_action.json()["code"], "action_not_found")

    def test_write_action_can_be_approved_but_never_executed(self):
        client = TestClient(create_app(orchestrator=build_approved_fixture_orchestrator(), seed_demo=True))
        diagnoses = client.get("/v1/troubleshooting/diagnoses").json()
        diagnosis = diagnoses[0]
        diagnosis_id = diagnosis["diagnosis_id"]
        write_action = next(
            action for action in diagnosis["recommended_actions"] if action["action_type"] == "manual_write"
        )

        confirmed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "on-call"},
        )
        self.assertEqual(confirmed.status_code, 200)

        approved = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/approve",
            json={"actor": "on-call", "reason": "DBA 已确认维护窗口"},
        )

        self.assertEqual(approved.status_code, 200)
        approved_action = next(
            action
            for action in approved.json()["recommended_actions"]
            if action["action_id"] == write_action["action_id"]
        )
        self.assertEqual(approved_action["approval_status"], "approved")
        self.assertEqual(approved_action["execution_status"], "blocked")
        self.assertEqual(approved.json()["pending_writes"], [])
        self.assertEqual(approved.json()["route_to_team"], "DBA 值班")

        execution = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/execute"
        )
        self.assertEqual(execution.status_code, 409)
        self.assertEqual(execution.json()["code"], "production_write_disabled")
        self.assertIn("not connected", execution.json()["detail"])

    def test_rehearsal_903001_runs_the_full_human_controlled_flow(self):
        client = TestClient(create_app(seed_demo=False))

        created = client.post(
            "/v1/troubleshooting/rehearsals/903001",
            json={"actor": "demo-on-call"},
        )

        self.assertEqual(created.status_code, 201)
        diagnosis = created.json()
        diagnosis_id = diagnosis["diagnosis_id"]
        self.assertTrue(diagnosis["rehearsal"])
        self.assertTrue(diagnosis["fixture_mode"])
        self.assertEqual(diagnosis["incident"]["system"], "CSDP-REHEARSAL")
        self.assertEqual(diagnosis["status"], "ready_for_human")
        self.assertEqual(diagnosis["confidence"], "high")
        self.assertTrue(diagnosis["case_id"].startswith("case-"))
        self.assertTrue(diagnosis["run_id"].startswith("run-"))
        self.assertTrue(any("合成演练" in warning for warning in diagnosis["warnings"]))
        self.assertEqual(client.get("/v1/troubleshooting/diagnoses").json(), [])
        self.assertEqual(
            len(client.get("/v1/troubleshooting/diagnoses?include_rehearsals=true").json()),
            1,
        )
        write_action = next(
            action for action in diagnosis["recommended_actions"] if action["action_type"] == "manual_write"
        )

        confirmed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "demo-on-call"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "confirmed")

        transferred = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/transfer",
            json={
                "actor": "demo-on-call",
                "target_team": "DBA 演练值班",
                "note": "请按演练恢复方案处理",
            },
        )
        self.assertEqual(transferred.status_code, 200)
        transfer = transferred.json()["transfers"][-1]
        self.assertEqual(transferred.json()["status"], "transferred")
        self.assertEqual(transfer["target_team"], "DBA 演练值班")
        self.assertEqual(transfer["context"]["case_id"], diagnosis["case_id"])
        self.assertEqual(transfer["context"]["run_id"], diagnosis["run_id"])
        self.assertEqual(
            set(transfer["context"]["evidence_ids"]),
            {"error-log", "mongo-metrics", "trace-db", "impact"},
        )
        self.assertEqual(transfer["context"]["root_cause"], diagnosis["root_cause"])

        approved = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/approve",
            json={"actor": "demo-dba", "reason": "演练维护窗口已确认"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["pending_writes"], [])

        recorded = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/record-outcome",
            json={
                "actor": "demo-dba",
                "outcome": "succeeded",
                "notes": "已在外部演练环境执行恢复操作",
                "recovery_verified": True,
            },
        )
        self.assertEqual(recorded.status_code, 200)
        self.assertEqual(recorded.json()["action_outcomes"][-1]["outcome"], "succeeded")
        self.assertTrue(recorded.json()["action_outcomes"][-1]["recovery_verified"])

        closed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "recovered",
                "summary": "MongoDB 连接恢复，业务探测通过",
                "recovery_verified": True,
                "sop_feedback": "补充连接池耗尽前的预警阈值",
                "create_knowledge_candidate": True,
            },
        )
        self.assertEqual(closed.status_code, 200)
        body = closed.json()
        self.assertEqual(body["status"], "closed")
        self.assertEqual(body["closure"]["outcome"], "recovered")
        self.assertTrue(body["closure"]["recovery_verified"])
        self.assertEqual(len(body["knowledge_candidates"]), 1)
        self.assertEqual(body["knowledge_candidates"][0]["status"], "candidate")
        self.assertEqual(body["knowledge_candidates"][0]["source_case_id"], diagnosis["case_id"])
        self.assertEqual(
            {action["action_id"] for action in body["knowledge_candidates"][0]["recommended_actions"]},
            {"retain-evidence", "contact-dba", "restart-mongodb"},
        )
        self.assertEqual(body["knowledge_candidates"][0]["action_outcomes"][0]["outcome"], "succeeded")
        self.assertTrue(body["knowledge_candidates"][0]["action_outcomes"][0]["recovery_verified"])
        self.assertEqual(
            body["closure"]["knowledge_candidate_id"],
            body["knowledge_candidates"][0]["candidate_id"],
        )
        timeline_text = " ".join(event["event"] for event in body["timeline"])
        for expected in ("人工确认", "结构化转派", "人工批准", "外部处置结果", "恢复验证", "关闭归档", "知识候选"):
            with self.subTest(expected=expected):
                self.assertIn(expected, timeline_text)

        execution = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/execute"
        )
        self.assertEqual(execution.status_code, 409)
        self.assertIn("not connected", execution.json()["detail"])

    def test_workflow_rejects_skipping_confirmation_or_recovery_verification(self):
        client = TestClient(create_app(seed_demo=False))
        diagnosis = client.post(
            "/v1/troubleshooting/rehearsals/903001",
            json={"actor": "demo-on-call"},
        ).json()
        diagnosis_id = diagnosis["diagnosis_id"]
        write_action = next(
            action for action in diagnosis["recommended_actions"] if action["action_type"] == "manual_write"
        )

        before_confirmation = [
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/transfer",
                json={"actor": "demo-on-call", "target_team": "DBA 演练值班", "note": "提前转派"},
            ),
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/approve",
                json={"actor": "demo-on-call", "reason": "提前批准"},
            ),
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
                json={
                    "actor": "demo-on-call",
                    "outcome": "recovered",
                    "summary": "跳过前序步骤",
                    "recovery_verified": True,
                },
            ),
        ]
        self.assertTrue(all(response.status_code == 409 for response in before_confirmation))

        confirmed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "demo-on-call"},
        )
        self.assertEqual(confirmed.status_code, 200)
        unverified_close = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "recovered",
                "summary": "尚未验证恢复",
                "recovery_verified": False,
            },
        )
        self.assertEqual(unverified_close.status_code, 409)
        self.assertIn("recovery verification", unverified_close.json()["detail"])

        pending_write_close = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "recovered",
                "summary": "试图绕过待审批写操作",
                "recovery_verified": True,
            },
        )
        self.assertEqual(pending_write_close.status_code, 409)
        self.assertIn("pending manual writes", pending_write_close.json()["detail"])

        unapproved_outcome = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/record-outcome",
            json={
                "actor": "demo-dba",
                "outcome": "succeeded",
                "notes": "试图跳过审批",
                "recovery_verified": True,
            },
        )
        self.assertEqual(unapproved_outcome.status_code, 409)
        self.assertIn("approved", unapproved_outcome.json()["detail"])

        approved = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/approve",
            json={"actor": "demo-on-call", "reason": "演练审批"},
        )
        self.assertEqual(approved.status_code, 200)
        close_without_outcome = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "recovered",
                "summary": "试图跳过外部处置结果",
                "recovery_verified": True,
            },
        )
        self.assertEqual(close_without_outcome.status_code, 409)
        self.assertIn("external outcome", close_without_outcome.json()["detail"])

    def test_false_positive_can_close_without_claiming_recovery(self):
        client = TestClient(create_app(seed_demo=False))
        diagnosis = client.post(
            "/v1/troubleshooting/rehearsals/903001",
            json={"actor": "demo-on-call"},
        ).json()
        diagnosis_id = diagnosis["diagnosis_id"]
        client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
            json={"actor": "demo-on-call"},
        )

        invalid_recovery_claim = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "false_positive",
                "summary": "误报不应声明恢复验证",
                "recovery_verified": True,
            },
        )
        self.assertEqual(invalid_recovery_claim.status_code, 409)
        self.assertIn("only recovered closure", invalid_recovery_claim.json()["detail"])

        closed = client.post(
            f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
            json={
                "actor": "demo-on-call",
                "outcome": "false_positive",
                "summary": "确认是演练误报，未执行恢复动作",
                "recovery_verified": False,
                "create_knowledge_candidate": True,
            },
        )

        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], "closed")
        self.assertEqual(closed.json()["closure"]["outcome"], "false_positive")
        self.assertFalse(closed.json()["closure"]["recovery_verified"])
        self.assertEqual(closed.json()["knowledge_candidates"][0]["action_outcomes"], [])

    def test_api_deduplicates_same_composite_incident_key(self):
        client = TestClient(create_app(seed_demo=False))
        base = {
            "system": "CSDP",
            "service": "csdp-wechat",
            "error_code": "903001",
            "title": "数据库访问异常",
            "severity": "P0",
            "completeness": "structured",
            "occurred_at": "2026-07-23T04:22:30+00:00",
            "intake_source": "webhook",
        }

        first = client.post(
            "/v1/troubleshooting/diagnoses",
            json={**base, "incident_id": "inc-first"},
        )
        duplicate = client.post(
            "/v1/troubleshooting/diagnoses",
            json={**base, "incident_id": "inc-duplicate"},
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json()["diagnosis_id"], first.json()["diagnosis_id"])
        self.assertEqual(len(client.get("/v1/troubleshooting/diagnoses").json()), 1)

    def test_workbench_is_served_with_utf8_document_metadata(self):
        response = self.client.get("/workbench")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("charset=utf-8", response.headers["content-type"].lower())
        self.assertIn('<meta charset="utf-8">', response.text.lower())
        self.assertIn('name="viewport"', response.text.lower())
        self.assertIn("/v1/troubleshooting/diagnoses", response.text)
        self.assertIn("/v1/troubleshooting/rehearsals/903001", response.text)
        self.assertIn("record-outcome", response.text)
        self.assertIn("结构化转派", response.text)
        self.assertIn("关闭并沉淀", response.text)
        self.assertIn("function(actionId)", response.text)
        self.assertIn("writes.every", response.text)
        self.assertIn("include_rehearsals=true", response.text)
        self.assertIn("接口模式", response.text)
        self.assertIn("MVP 仅记录人工批准", response.text)
        self.assertNotIn("转派（未接入）", response.text)


if __name__ == "__main__":
    unittest.main()
