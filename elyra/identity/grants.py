"""One-time operator grant tokens for self identity promote (K14).

Scope: load/mint/consume under data/runtime/identity_grants.json.
In scope: grant_ + 32 hex; uses_remaining; optional expires_at; active set;
  ELYRA_SELF_PROMOTE_GRANT env as extra accepted token in the active set.
Out of scope: Glass API routes, evaluate_promote_gate, store.promote.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from elyra.config import ElyraPaths
from elyra.identity.layout import load_json_object, utc_now_iso, write_json_atomic

logger = logging.getLogger(__name__)

GRANTS_REL = Path("runtime") / "identity_grants.json"
ENV_SELF_PROMOTE_GRANT = "ELYRA_SELF_PROMOTE_GRANT"
SCHEMA_VERSION = 1

# Module lock serializes mint/consume across callers sharing a process.
_LOCK = threading.RLock()


def grants_path(paths: ElyraPaths) -> Path:
    return paths.data_dir / GRANTS_REL


def _empty_doc() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "tokens": []}


def _parse_expires(expires_at: str | None) -> datetime | None:
    if not expires_at or not isinstance(expires_at, str):
        return None
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp.astimezone(UTC)


def _is_expired(expires_at: str | None, *, now: datetime | None = None) -> bool:
    exp = _parse_expires(expires_at)
    if exp is None:
        # Missing/invalid expires_at → treat as non-expiring when field absent;
        # invalid string is treated as expired (fail closed).
        if expires_at is None or expires_at == "":
            return False
        return True
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    return clock >= exp


def _normalize_doc(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return _empty_doc()
    tokens = raw.get("tokens")
    if not isinstance(tokens, list):
        tokens = []
    clean: list[dict[str, Any]] = []
    for row in tokens:
        if not isinstance(row, dict):
            continue
        token = row.get("token")
        if not isinstance(token, str) or not token.strip():
            continue
        uses = row.get("uses_remaining", 0)
        try:
            uses_i = int(uses)
        except (TypeError, ValueError):
            uses_i = 0
        clean.append(
            {
                "token": token.strip(),
                "created_at": row.get("created_at"),
                "expires_at": row.get("expires_at"),
                "uses_remaining": uses_i,
                "note": row.get("note"),
            }
        )
    return {
        "schema_version": int(raw.get("schema_version") or SCHEMA_VERSION),
        "tokens": clean,
    }


def load_grants(paths: ElyraPaths) -> dict[str, Any]:
    """Load grants file (normalized). Missing → empty doc."""
    with _LOCK:
        return _normalize_doc(load_json_object(grants_path(paths)))


def _env_grant_token() -> str | None:
    raw = os.environ.get(ENV_SELF_PROMOTE_GRANT, "").strip()
    return raw or None


def active_token_set(
    doc: Mapping[str, Any],
    *,
    now: datetime | None = None,
    include_env: bool = True,
) -> frozenset[str]:
    """Tokens with uses_remaining > 0 and not expired (+ optional env dogfood)."""
    out: set[str] = set()
    tokens = doc.get("tokens") or []
    if isinstance(tokens, list):
        for row in tokens:
            if not isinstance(row, dict):
                continue
            token = row.get("token")
            if not isinstance(token, str) or not token.strip():
                continue
            try:
                uses = int(row.get("uses_remaining") or 0)
            except (TypeError, ValueError):
                uses = 0
            if uses <= 0:
                continue
            if _is_expired(row.get("expires_at"), now=now):
                continue
            out.add(token.strip())
    if include_env:
        env_tok = _env_grant_token()
        if env_tok:
            out.add(env_tok)
    return frozenset(out)


def load_active_token_set(
    paths: ElyraPaths,
    *,
    include_env: bool = True,
) -> frozenset[str]:
    """Active grant tokens from disk (+ optional ELYRA_SELF_PROMOTE_GRANT)."""
    return active_token_set(load_grants(paths), include_env=include_env)


def first_active_token(
    paths: ElyraPaths,
    *,
    include_env: bool = False,
) -> str | None:
    """First non-expired file token with uses_remaining > 0 (Glass resolve).

    Does not include env by default — env is for active-set membership only
    unless the caller passes it as body.grant_token.
    """
    doc = load_grants(paths)
    tokens = doc.get("tokens") or []
    if not isinstance(tokens, list):
        return None
    for row in tokens:
        if not isinstance(row, dict):
            continue
        token = row.get("token")
        if not isinstance(token, str) or not token.strip():
            continue
        try:
            uses = int(row.get("uses_remaining") or 0)
        except (TypeError, ValueError):
            uses = 0
        if uses <= 0:
            continue
        if _is_expired(row.get("expires_at")):
            continue
        return token.strip()
    if include_env:
        return _env_grant_token()
    return None


def mint_grant(
    paths: ElyraPaths,
    *,
    note: str | None = None,
    expires_at: str | None = None,
    uses: int = 1,
) -> dict[str, Any]:
    """Append one-time grant token; return raw token once.

    Returns ``{ok, token, created_at, expires_at, uses_remaining, note}``
    or ``{ok: False, error: ...}``.
    """
    if uses < 1:
        return {"ok": False, "error": "invalid_uses"}

    token = "grant_" + secrets.token_hex(16)
    created = utc_now_iso()
    row = {
        "token": token,
        "created_at": created,
        "expires_at": expires_at,
        "uses_remaining": int(uses),
        "note": note,
    }
    with _LOCK:
        path = grants_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = _normalize_doc(load_json_object(path))
        tokens = list(doc.get("tokens") or [])
        tokens.append(row)
        doc["tokens"] = tokens
        doc["schema_version"] = SCHEMA_VERSION
        try:
            write_json_atomic(path, doc)
        except OSError as exc:
            logger.exception("mint_grant write failed")
            return {"ok": False, "error": f"grant_write_failed:{type(exc).__name__}"}
    return {
        "ok": True,
        "token": token,
        "created_at": created,
        "expires_at": expires_at,
        "uses_remaining": int(uses),
        "note": note,
    }


def consume_grant(
    paths: ElyraPaths,
    token: str,
) -> dict[str, Any]:
    """Atomically decrement uses_remaining for token.

    Order for callers: resolve → gate → **consume** → promote.
    Returns ok True on success; error grant_exhausted / grant_expired /
    grant_missing / grant_write_failed.
    """
    if not isinstance(token, str) or not token.strip():
        return {"ok": False, "error": "grant_missing"}
    token = token.strip()

    # Env one-shot: accept without file row (dogfood); do not rewrite file.
    env_tok = _env_grant_token()
    if env_tok and token == env_tok:
        # Still require it to be "active"; env has no uses counter — treat as
        # always available for gate membership; consume is a no-op success so
        # promote can proceed. Product Glass path prefers file tokens.
        return {"ok": True, "token": token, "source": "env", "uses_remaining": 0}

    with _LOCK:
        path = grants_path(paths)
        doc = _normalize_doc(load_json_object(path))
        tokens = list(doc.get("tokens") or [])
        found_idx: int | None = None
        for i, row in enumerate(tokens):
            if not isinstance(row, dict):
                continue
            if row.get("token") == token:
                found_idx = i
                break
        if found_idx is None:
            return {"ok": False, "error": "grant_missing"}

        row = dict(tokens[found_idx])
        if _is_expired(row.get("expires_at")):
            return {"ok": False, "error": "grant_expired"}

        try:
            uses = int(row.get("uses_remaining") or 0)
        except (TypeError, ValueError):
            uses = 0
        if uses <= 0:
            return {"ok": False, "error": "grant_exhausted"}

        row["uses_remaining"] = uses - 1
        tokens[found_idx] = row
        doc["tokens"] = tokens
        doc["schema_version"] = SCHEMA_VERSION
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, doc)
        except OSError as exc:
            logger.exception("consume_grant write failed")
            return {"ok": False, "error": f"grant_write_failed:{type(exc).__name__}"}

        return {
            "ok": True,
            "token": token,
            "source": "file",
            "uses_remaining": row["uses_remaining"],
        }
