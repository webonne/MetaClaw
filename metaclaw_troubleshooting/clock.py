"""Shared UTC timestamp helpers for troubleshooting records."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
