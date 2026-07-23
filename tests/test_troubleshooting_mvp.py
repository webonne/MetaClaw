import unittest

from fastapi.testclient import TestClient

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
    ActionType,
    ApprovalStatus,
    Confidence,
    DiagnosisStatus,
    EvidenceStatus,
    ExecutionStatus,
    IncidentCompleteness,
    IncidentContext,
    RouteMode,
)
from metaclaw_troubleshooting.orchestrator import TroubleshootingOrchestrator
from metaclaw_troubleshooting.repository import (
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
        self.assertIn("requires new evidence", response.json()["detail"])

    def test_write_action_can_be_approved_but_never_executed(self):
        client = TestClient(create_app(orchestrator=build_approved_fixture_orchestrator(), seed_demo=True))
        diagnoses = client.get("/v1/troubleshooting/diagnoses").json()
        diagnosis = diagnoses[0]
        diagnosis_id = diagnosis["diagnosis_id"]
        write_action = next(
            action for action in diagnosis["recommended_actions"] if action["action_type"] == "manual_write"
        )

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
        self.assertIn("not connected", execution.json()["detail"])

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
        self.assertIn("接口模式", response.text)
        self.assertIn("MVP 仅记录人工批准", response.text)


if __name__ == "__main__":
    unittest.main()
