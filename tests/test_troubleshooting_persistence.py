import sqlite3
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from fastapi.testclient import TestClient

from metaclaw_troubleshooting.api import create_app
from metaclaw_troubleshooting.factory import fixture_incident_903001
from metaclaw_troubleshooting.fixtures import FixtureEvidenceCollector, fixture_sop_903001
from metaclaw_troubleshooting.models import (
    ActionOutcomeRequest,
    ActionOutcomeStatus,
    ActionType,
    ApprovalRequest,
    CloseRequest,
    ClosureOutcome,
    DiagnosisStatus,
    TransferRequest,
)
from metaclaw_troubleshooting.module import (
    ApproveAction,
    CloseDiagnosis,
    ConfirmDiagnosis,
    RecordActionOutcome,
    TransferDiagnosis,
    TroubleshootingModule,
)
from metaclaw_troubleshooting.orchestrator import TroubleshootingOrchestrator
from metaclaw_troubleshooting.ports import RepositoryReadiness, RepositoryUnavailable
from metaclaw_troubleshooting.repository import InMemorySopRepository, SQLiteDiagnosisRepository


def build_approved_fixture_orchestrator() -> TroubleshootingOrchestrator:
    sop = fixture_sop_903001()
    sop.status = "approved"
    sop.verified = True
    return TroubleshootingOrchestrator(
        sop_repository=InMemorySopRepository([sop]),
        evidence_collector=FixtureEvidenceCollector(),
    )


def run_closed_flow(module: TroubleshootingModule, incident_id: str = "sqlite-recovery"):
    incident = fixture_incident_903001().model_copy(
        update={
            "incident_id": incident_id,
            "occurred_at": "2026-07-23T05:00:00+00:00",
        }
    )
    diagnosis = module.diagnose(incident)
    write_action = next(
        action for action in diagnosis.recommended_actions if action.action_type == ActionType.MANUAL_WRITE
    )
    module.apply(diagnosis.diagnosis_id, ConfirmDiagnosis(actor="on-call"))
    module.apply(
        diagnosis.diagnosis_id,
        TransferDiagnosis(
            request=TransferRequest(
                actor="on-call",
                target_team="DBA 值班",
                note="携带证据转派",
            )
        ),
    )
    module.apply(
        diagnosis.diagnosis_id,
        ApproveAction(
            action_id=write_action.action_id,
            request=ApprovalRequest(actor="dba", reason="维护窗口已确认"),
        ),
    )
    module.apply(
        diagnosis.diagnosis_id,
        RecordActionOutcome(
            action_id=write_action.action_id,
            request=ActionOutcomeRequest(
                actor="dba",
                outcome=ActionOutcomeStatus.SUCCEEDED,
                notes="外部恢复操作完成",
                recovery_verified=True,
            ),
        ),
    )
    return module.apply(
        diagnosis.diagnosis_id,
        CloseDiagnosis(
            request=CloseRequest(
                actor="on-call",
                outcome=ClosureOutcome.RECOVERED,
                summary="连接恢复且探测通过",
                recovery_verified=True,
                sop_feedback="补充连接池预警阈值",
                create_knowledge_candidate=True,
            )
        ),
    )


class SQLiteDiagnosisRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nested" / "troubleshooting.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_restart_restores_full_aggregate_and_transactional_outbox(self):
        repository = SQLiteDiagnosisRepository(self.database_path)
        module = TroubleshootingModule(build_approved_fixture_orchestrator(), repository)
        closed = run_closed_flow(module)
        repository.close()

        reopened = SQLiteDiagnosisRepository(self.database_path)
        restored = reopened.get(closed.diagnosis_id)
        publications = reopened.pending_publications()
        readiness = reopened.readiness()

        self.assertIsNotNone(restored)
        self.assertEqual(restored.model_dump(mode="json"), closed.model_dump(mode="json"))
        self.assertEqual(restored.status, DiagnosisStatus.CLOSED)
        self.assertEqual(len(restored.transfers), 1)
        self.assertEqual(len(restored.action_outcomes), 1)
        self.assertTrue(restored.closure.recovery_verified)
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0].candidate_id, closed.knowledge_candidates[0].candidate_id)
        self.assertEqual(
            publications[0].payload.model_dump(mode="json"),
            closed.knowledge_candidates[0].model_dump(mode="json"),
        )
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.adapter, "sqlite")
        self.assertTrue(readiness.persistent)
        self.assertEqual(readiness.schema_version, 1)
        self.assertEqual(readiness.pending_publications, 1)
        reopened.close()

    def test_composite_idempotency_survives_restart(self):
        first_repository = SQLiteDiagnosisRepository(self.database_path)
        first_module = TroubleshootingModule(build_approved_fixture_orchestrator(), first_repository)
        first_incident = fixture_incident_903001().model_copy(
            update={
                "incident_id": "first-after-restart",
                "occurred_at": "2026-07-23T05:02:01+00:00",
            }
        )
        first = first_module.diagnose(first_incident)
        first_repository.close()

        second_repository = SQLiteDiagnosisRepository(self.database_path)
        second_module = TroubleshootingModule(build_approved_fixture_orchestrator(), second_repository)
        duplicate_incident = first_incident.model_copy(update={"incident_id": "duplicate-after-restart"})
        duplicate = second_module.diagnose(duplicate_incident)

        self.assertEqual(duplicate.diagnosis_id, first.diagnosis_id)
        self.assertEqual(duplicate.incident.incident_id, "first-after-restart")
        self.assertEqual(len(second_repository.list()), 1)
        second_repository.close()

    def test_outbox_failure_rolls_back_diagnosis_closure(self):
        repository = SQLiteDiagnosisRepository(self.database_path)
        module = TroubleshootingModule(build_approved_fixture_orchestrator(), repository)
        diagnosis = module.diagnose(
            fixture_incident_903001().model_copy(
                update={
                    "incident_id": "outbox-rollback",
                    "occurred_at": "2026-07-23T05:10:00+00:00",
                }
            )
        )
        module.apply(diagnosis.diagnosis_id, ConfirmDiagnosis(actor="on-call"))
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_knowledge_outbox
                BEFORE INSERT ON knowledge_outbox
                BEGIN
                    SELECT RAISE(ABORT, 'simulated outbox failure');
                END
                """
            )

        with self.assertRaises(RepositoryUnavailable):
            module.apply(
                diagnosis.diagnosis_id,
                CloseDiagnosis(
                    request=CloseRequest(
                        actor="on-call",
                        outcome=ClosureOutcome.FALSE_POSITIVE,
                        summary="演练误报",
                        create_knowledge_candidate=True,
                    )
                ),
            )

        unchanged = repository.get(diagnosis.diagnosis_id)
        self.assertEqual(unchanged.status, DiagnosisStatus.CONFIRMED)
        self.assertIsNone(unchanged.closure)
        self.assertEqual(unchanged.knowledge_candidates, [])
        self.assertEqual(repository.pending_publications(), [])
        repository.close()


class UnavailableDiagnosisRepository:
    def add(self, _diagnosis):
        raise RepositoryUnavailable("simulated storage outage")

    def update(self, _diagnosis_id, _mutation):
        raise RepositoryUnavailable("simulated storage outage")

    def get(self, _diagnosis_id):
        raise RepositoryUnavailable("simulated storage outage")

    def list(self, *, include_rehearsals=False):
        raise RepositoryUnavailable("simulated storage outage")

    def pending_publications(self):
        raise RepositoryUnavailable("simulated storage outage")

    def readiness(self):
        return RepositoryReadiness(
            ready=False,
            adapter="unavailable-test",
            persistent=True,
            schema_version=None,
            pending_publications=None,
            detail="simulated storage outage",
        )


class TroubleshootingPersistenceApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "troubleshooting.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_readyz_capabilities_and_api_state_survive_app_restart(self):
        first_app = create_app(seed_demo=False, database_path=self.database_path)
        with TestClient(first_app) as client:
            ready = client.get("/readyz")
            capabilities = client.get("/v1/troubleshooting/capabilities")
            created = client.post(
                "/v1/troubleshooting/rehearsals/903001",
                json={"actor": "demo-on-call"},
            ).json()
            diagnosis_id = created["diagnosis_id"]
            write_action = next(
                action for action in created["recommended_actions"] if action["action_type"] == "manual_write"
            )
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/confirm",
                json={"actor": "demo-on-call"},
            )
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/approve",
                json={"actor": "demo-dba", "reason": "演练维护窗口"},
            )
            client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/actions/{write_action['action_id']}/record-outcome",
                json={
                    "actor": "demo-dba",
                    "outcome": "succeeded",
                    "notes": "外部演练完成",
                    "recovery_verified": True,
                },
            )
            closed = client.post(
                f"/v1/troubleshooting/diagnoses/{diagnosis_id}/close",
                json={
                    "actor": "demo-on-call",
                    "outcome": "recovered",
                    "summary": "恢复验证通过",
                    "recovery_verified": True,
                    "create_knowledge_candidate": True,
                },
            ).json()

        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json()["ok"])
        self.assertEqual(ready.json()["storage"]["adapter"], "sqlite")
        self.assertEqual(capabilities.status_code, 200)
        self.assertEqual(capabilities.json()["runtime_mode"], "fixture")
        self.assertTrue(capabilities.json()["storage"]["persistent"])
        self.assertEqual(capabilities.json()["knowledge_publication"]["mode"], "transactional_outbox")
        self.assertFalse(capabilities.json()["knowledge_publication"]["publisher_connected"])
        self.assertFalse(capabilities.json()["write_execution_enabled"])
        self.assertFalse(capabilities.json()["trusted_identity"])

        second_app = create_app(seed_demo=False, database_path=self.database_path)
        with TestClient(second_app) as client:
            restored = client.get(f"/v1/troubleshooting/diagnoses/{diagnosis_id}")
            after_restart = client.get("/v1/troubleshooting/capabilities").json()

        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["case_id"], closed["case_id"])
        self.assertEqual(restored.json()["run_id"], closed["run_id"])
        self.assertEqual(restored.json()["status"], "closed")
        self.assertEqual(restored.json()["closure"], closed["closure"])
        write_action_after_restart = next(
            action
            for action in restored.json()["recommended_actions"]
            if action["action_type"] == "manual_write"
        )
        self.assertEqual(write_action_after_restart["approval_status"], "approved")
        self.assertEqual(after_restart["knowledge_publication"]["pending"], 1)

    def test_storage_outage_is_not_ready_and_returns_traceable_503(self):
        app = create_app(
            seed_demo=False,
            diagnosis_repository=UnavailableDiagnosisRepository(),
        )
        client = TestClient(app)

        ready = client.get("/readyz")
        created = client.post(
            "/v1/troubleshooting/diagnoses",
            json={
                "incident_id": "storage-down",
                "system": "CSDP",
                "service": "csdp-wechat",
                "error_code": "903001",
            },
        )

        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["code"], "storage_unavailable")
        self.assertIn("error_id", ready.json())
        self.assertEqual(created.status_code, 503)
        self.assertEqual(created.json()["code"], "storage_unavailable")
        self.assertIn("error_id", created.json())

    def test_workbench_is_a_packaged_resource_and_displays_runtime_capabilities(self):
        workbench = resources.files("metaclaw_troubleshooting").joinpath(
            "static",
            "console-workbench.html",
        )

        self.assertTrue(workbench.is_file())
        html = workbench.read_text(encoding="utf-8")
        self.assertIn("/readyz", html)
        self.assertIn("/v1/troubleshooting/capabilities", html)
        response = TestClient(create_app(seed_demo=False)).get("/workbench")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, html)


if __name__ == "__main__":
    unittest.main()
