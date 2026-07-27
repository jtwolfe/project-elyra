"""Shared version_id minting for identity and package VCS.

Only mint/regex/limit live here. Identity file GC (versions/*.md) stays in
``elyra.identity.layout``. Package directory GC lives in ``elyra.tools.promote``.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

# Public version_id == archive id / filename stem (identity .md or package dir).
VERSION_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{6}$")

VERSION_GC_LIMIT = 50


def mint_version_id(now: datetime | None = None) -> str:
    """Return e.g. ``20260726T153045Z_a1b2c3`` (filename / directory stem)."""
    ts = now if now is not None else datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    compact = ts.strftime("%Y%m%dT%H%M%SZ")
    return f"{compact}_{secrets.token_hex(3)}"


__all__ = [
    "VERSION_GC_LIMIT",
    "VERSION_ID_RE",
    "mint_version_id",
]
