"""Fail-closed repository adapters for SOP and diagnosis state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from threading import RLock
from typing import Iterator, List

from pydantic import ValidationError

from .clock import utc_now
from .models import (
    Diagnosis,
    KnowledgeCandidate,
    KnowledgePublication,
    KnowledgePublicationStatus,
    SopEntry,
)
from .ports import RepositoryReadiness, RepositoryUnavailable


class SopKeyCollisionError(ValueError):
    """Raised when a D1 routing key has more than one SOP candidate."""


def routing_key(system: str, error_code: str) -> tuple[str, str]:
    return system.strip().lower(), error_code.strip()


class InMemorySopRepository:
    def __init__(self, entries: Iterable[SopEntry]):
        self._entries: dict[tuple[str, str], SopEntry] = {}
        for entry in entries:
            key = routing_key(entry.system, entry.error_code)
            if key in self._entries:
                raise SopKeyCollisionError(f"duplicate SOP routing key: ({key[0]},{key[1]})")
            self._entries[key] = entry.model_copy(deep=True)

    def get(self, system: str, error_code: str) -> SopEntry | None:
        entry = self._entries.get(routing_key(system, error_code))
        return entry.model_copy(deep=True) if entry else None


class InMemoryDiagnosisRepository:
    """Thread-safe local Adapter with copy isolation and composite idempotency."""

    def __init__(self) -> None:
        self._items: dict[str, Diagnosis] = {}
        self._idempotency_keys: dict[tuple[str, str, str, str], str] = {}
        self._diagnosis_keys: dict[str, tuple[str, str, str, str] | None] = {}
        self._publications: dict[str, KnowledgePublication] = {}
        self._lock = RLock()

    def add(self, diagnosis: Diagnosis) -> Diagnosis:
        with self._lock:
            existing = self._items.get(diagnosis.diagnosis_id)
            if existing is not None:
                return existing.model_copy(deep=True)
            idempotency_key = self._idempotency_key(diagnosis)
            if idempotency_key is not None:
                existing_id = self._idempotency_keys.get(idempotency_key)
                if existing_id is not None:
                    return self._items[existing_id].model_copy(deep=True)
            stored = diagnosis.model_copy(deep=True)
            self._items[stored.diagnosis_id] = stored
            self._diagnosis_keys[stored.diagnosis_id] = idempotency_key
            if idempotency_key is not None:
                self._idempotency_keys[idempotency_key] = stored.diagnosis_id
            self._enqueue_candidates_locked(stored, previous_candidate_ids=set())
            return stored.model_copy(deep=True)

    def update(
        self,
        diagnosis_id: str,
        mutation: Callable[[Diagnosis], Diagnosis],
    ) -> Diagnosis | None:
        """Apply one domain mutation atomically against the latest stored copy."""

        with self._lock:
            existing = self._items.get(diagnosis_id)
            if existing is None:
                return None
            updated = mutation(existing.model_copy(deep=True))
            if updated.diagnosis_id != diagnosis_id:
                raise ValueError("diagnosis mutation cannot change diagnosis_id")
            return self._save_locked(updated)

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        with self._lock:
            diagnosis = self._items.get(diagnosis_id)
            return diagnosis.model_copy(deep=True) if diagnosis else None

    def list(self, *, include_rehearsals: bool = False) -> list[Diagnosis]:
        with self._lock:
            items = list(self._items.values())
            if not include_rehearsals:
                items = [item for item in items if not item.rehearsal]
            return [item.model_copy(deep=True) for item in items]

    def pending_publications(self, *, limit: int = 100) -> List[KnowledgePublication]:
        with self._lock:
            if limit <= 0:
                return []
            pending = [
                publication
                for publication in self._publications.values()
                if publication.status != KnowledgePublicationStatus.PUBLISHED
            ]
            return [item.model_copy(deep=True) for item in pending[:limit]]

    def readiness(self) -> RepositoryReadiness:
        with self._lock:
            return RepositoryReadiness(
                ready=True,
                adapter="memory",
                persistent=False,
                schema_version=0,
                pending_publications=len(self.pending_publications()),
            )

    def _save_locked(self, diagnosis: Diagnosis) -> Diagnosis:
        existing = self._items.get(diagnosis.diagnosis_id)
        if existing is None:
            raise KeyError(f"diagnosis not found: {diagnosis.diagnosis_id}")
        old_key = self._diagnosis_keys[diagnosis.diagnosis_id]
        if self._idempotency_identity(existing) == self._idempotency_identity(diagnosis):
            new_key = old_key
        else:
            new_key = self._idempotency_key(diagnosis)
        if new_key is not None:
            conflicting_id = self._idempotency_keys.get(new_key)
            if conflicting_id is not None and conflicting_id != diagnosis.diagnosis_id:
                raise ValueError("diagnosis idempotency key belongs to another diagnosis")
        if old_key is not None and old_key != new_key:
            self._idempotency_keys.pop(old_key, None)
        stored = diagnosis.model_copy(deep=True)
        self._items[stored.diagnosis_id] = stored
        self._diagnosis_keys[stored.diagnosis_id] = new_key
        if new_key is not None:
            self._idempotency_keys[new_key] = stored.diagnosis_id
        self._enqueue_candidates_locked(
            stored,
            previous_candidate_ids={item.candidate_id for item in existing.knowledge_candidates},
        )
        return stored.model_copy(deep=True)

    def _enqueue_candidates_locked(
        self,
        diagnosis: Diagnosis,
        *,
        previous_candidate_ids: set[str],
    ) -> None:
        for candidate in diagnosis.knowledge_candidates:
            if candidate.candidate_id in previous_candidate_ids:
                continue
            publication = _new_publication(diagnosis, candidate)
            self._publications.setdefault(publication.publication_id, publication)

    @staticmethod
    def _idempotency_key(
        diagnosis: Diagnosis,
    ) -> tuple[str, str, str, str] | None:
        if diagnosis.rehearsal:
            return None
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

    @staticmethod
    def _idempotency_identity(
        diagnosis: Diagnosis,
    ) -> tuple[bool, str, str, str, str | None]:
        incident = diagnosis.incident
        return (
            diagnosis.rehearsal,
            incident.system.strip().lower(),
            (incident.error_code or "").strip(),
            incident.service.strip().lower(),
            incident.occurred_at,
        )


class SQLiteDiagnosisRepository:
    """SQLite adapter that commits aggregate state and knowledge outbox atomically."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self._lock = RLock()
        self._closed = False
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.database_path,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._run_migrations()
        except (OSError, sqlite3.Error) as error:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise RepositoryUnavailable("troubleshooting storage unavailable") from error

    def add(self, diagnosis: Diagnosis) -> Diagnosis:
        with self._lock, self._transaction():
            existing = self._connection.execute(
                "SELECT payload_json FROM diagnoses WHERE diagnosis_id = ?",
                (diagnosis.diagnosis_id,),
            ).fetchone()
            if existing is not None:
                return self._decode_diagnosis(existing["payload_json"])

            idempotency_key = _encode_idempotency_key(_idempotency_key(diagnosis))
            if idempotency_key is not None:
                duplicate = self._connection.execute(
                    "SELECT payload_json FROM diagnoses WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if duplicate is not None:
                    return self._decode_diagnosis(duplicate["payload_json"])

            stored = diagnosis.model_copy(deep=True)
            timestamp = utc_now()
            self._connection.execute(
                """
                INSERT INTO diagnoses (
                    diagnosis_id,
                    idempotency_key,
                    rehearsal,
                    payload_json,
                    created_at,
                    updated_at,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    stored.diagnosis_id,
                    idempotency_key,
                    int(stored.rehearsal),
                    stored.model_dump_json(),
                    timestamp,
                    timestamp,
                ),
            )
            self._enqueue_candidates_locked(stored, previous_candidate_ids=set())
            return stored.model_copy(deep=True)

    def update(
        self,
        diagnosis_id: str,
        mutation: Callable[[Diagnosis], Diagnosis],
    ) -> Diagnosis | None:
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT payload_json, idempotency_key, revision
                FROM diagnoses
                WHERE diagnosis_id = ?
                """,
                (diagnosis_id,),
            ).fetchone()
            if row is None:
                return None

            existing = self._decode_diagnosis(row["payload_json"])
            updated = mutation(existing.model_copy(deep=True))
            if updated.diagnosis_id != diagnosis_id:
                raise ValueError("diagnosis mutation cannot change diagnosis_id")
            if _idempotency_identity(existing) == _idempotency_identity(updated):
                idempotency_key = row["idempotency_key"]
            else:
                idempotency_key = _encode_idempotency_key(_idempotency_key(updated))
            self._connection.execute(
                """
                UPDATE diagnoses
                SET idempotency_key = ?, rehearsal = ?, payload_json = ?, updated_at = ?, revision = ?
                WHERE diagnosis_id = ?
                """,
                (
                    idempotency_key,
                    int(updated.rehearsal),
                    updated.model_dump_json(),
                    utc_now(),
                    int(row["revision"]) + 1,
                    diagnosis_id,
                ),
            )
            self._enqueue_candidates_locked(
                updated,
                previous_candidate_ids={item.candidate_id for item in existing.knowledge_candidates},
            )
            return updated.model_copy(deep=True)

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT payload_json FROM diagnoses WHERE diagnosis_id = ?",
                    (diagnosis_id,),
                ).fetchone()
                return self._decode_diagnosis(row["payload_json"]) if row is not None else None
            except sqlite3.Error as error:
                raise RepositoryUnavailable("troubleshooting storage unavailable") from error

    def list(self, *, include_rehearsals: bool = False) -> list[Diagnosis]:
        with self._lock:
            try:
                if include_rehearsals:
                    rows = self._connection.execute(
                        "SELECT payload_json FROM diagnoses ORDER BY created_at, diagnosis_id"
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        """
                        SELECT payload_json
                        FROM diagnoses
                        WHERE rehearsal = 0
                        ORDER BY created_at, diagnosis_id
                        """
                    ).fetchall()
                return [self._decode_diagnosis(row["payload_json"]) for row in rows]
            except sqlite3.Error as error:
                raise RepositoryUnavailable("troubleshooting storage unavailable") from error

    def pending_publications(self, *, limit: int = 100) -> List[KnowledgePublication]:
        if limit <= 0:
            return []
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    SELECT publication_id, contract_version, diagnosis_id, candidate_id,
                           payload_json, status, attempts, last_error, created_at, updated_at
                    FROM knowledge_outbox
                    WHERE status IN ('pending', 'failed')
                    ORDER BY created_at, publication_id
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [self._decode_publication(row) for row in rows]
            except sqlite3.Error as error:
                raise RepositoryUnavailable("troubleshooting storage unavailable") from error

    def readiness(self) -> RepositoryReadiness:
        with self._lock:
            try:
                schema_version = self._connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
                pending = self._connection.execute(
                    "SELECT COUNT(*) FROM knowledge_outbox WHERE status IN ('pending', 'failed')"
                ).fetchone()[0]
                return RepositoryReadiness(
                    ready=True,
                    adapter="sqlite",
                    persistent=True,
                    schema_version=int(schema_version),
                    pending_publications=int(pending),
                )
            except (sqlite3.Error, TypeError, ValueError):
                return RepositoryReadiness(
                    ready=False,
                    adapter="sqlite",
                    persistent=True,
                    schema_version=None,
                    pending_publications=None,
                    detail="troubleshooting storage unavailable",
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _run_migrations(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row[0])
            for row in self._connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        migration_directory = resources.files("metaclaw_troubleshooting").joinpath("migrations")
        migrations = sorted(
            (item for item in migration_directory.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        )
        for migration in migrations:
            version_text = migration.name.split("_", 1)[0]
            try:
                version = int(version_text)
            except ValueError as error:
                raise RepositoryUnavailable(f"invalid migration filename: {migration.name}") from error
            if version in applied:
                continue
            try:
                self._connection.executescript(migration.read_text(encoding="utf-8"))
            except (OSError, sqlite3.Error) as error:
                self._rollback_quietly()
                raise RepositoryUnavailable("troubleshooting schema migration failed") from error

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except sqlite3.Error as error:
            self._rollback_quietly()
            raise RepositoryUnavailable("troubleshooting storage unavailable") from error
        except BaseException:
            self._rollback_quietly()
            raise

    def _rollback_quietly(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.Error:
            pass

    def _enqueue_candidates_locked(
        self,
        diagnosis: Diagnosis,
        *,
        previous_candidate_ids: set[str],
    ) -> None:
        for candidate in diagnosis.knowledge_candidates:
            if candidate.candidate_id in previous_candidate_ids:
                continue
            publication = _new_publication(diagnosis, candidate)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_outbox (
                    publication_id,
                    candidate_id,
                    diagnosis_id,
                    contract_version,
                    payload_json,
                    status,
                    attempts,
                    last_error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication.publication_id,
                    publication.candidate_id,
                    publication.diagnosis_id,
                    publication.contract_version,
                    publication.payload.model_dump_json(),
                    publication.status.value,
                    publication.attempts,
                    publication.last_error,
                    publication.created_at,
                    publication.updated_at,
                ),
            )

    @staticmethod
    def _decode_diagnosis(payload_json: str) -> Diagnosis:
        try:
            return Diagnosis.model_validate_json(payload_json)
        except ValidationError as error:
            raise RepositoryUnavailable("stored diagnosis failed schema validation") from error

    @staticmethod
    def _decode_publication(row: sqlite3.Row) -> KnowledgePublication:
        try:
            candidate = KnowledgeCandidate.model_validate_json(row["payload_json"])
            return KnowledgePublication(
                publication_id=row["publication_id"],
                contract_version=row["contract_version"],
                diagnosis_id=row["diagnosis_id"],
                candidate_id=row["candidate_id"],
                payload=candidate,
                status=row["status"],
                attempts=row["attempts"],
                last_error=row["last_error"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except ValidationError as error:
            raise RepositoryUnavailable("stored knowledge publication failed schema validation") from error


def _new_publication(diagnosis: Diagnosis, candidate: KnowledgeCandidate) -> KnowledgePublication:
    timestamp = utc_now()
    return KnowledgePublication(
        publication_id=f"publication-{candidate.candidate_id}",
        diagnosis_id=diagnosis.diagnosis_id,
        candidate_id=candidate.candidate_id,
        payload=candidate.model_copy(deep=True),
        created_at=timestamp,
        updated_at=timestamp,
    )


def _encode_idempotency_key(key: tuple[str, str, str, str] | None) -> str | None:
    if key is None:
        return None
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


def _idempotency_key(diagnosis: Diagnosis) -> tuple[str, str, str, str] | None:
    return InMemoryDiagnosisRepository._idempotency_key(diagnosis)


def _idempotency_identity(diagnosis: Diagnosis) -> tuple[bool, str, str, str, str | None]:
    return InMemoryDiagnosisRepository._idempotency_identity(diagnosis)
