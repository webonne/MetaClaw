"""Fail-closed SOP repository implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from threading import RLock

from .models import Diagnosis, SopEntry


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
        return stored.model_copy(deep=True)

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
