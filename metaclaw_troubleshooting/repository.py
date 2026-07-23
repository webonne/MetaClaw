"""Fail-closed SOP repository implementations."""

from __future__ import annotations

from collections.abc import Iterable

from .models import SopEntry


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
