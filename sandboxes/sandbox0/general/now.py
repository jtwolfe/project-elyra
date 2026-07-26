"""Builtin general seed — UTC timestamp (read_only)."""

from __future__ import annotations

from datetime import UTC, datetime


def run() -> dict:
    return {"utc": datetime.now(UTC).isoformat()}
