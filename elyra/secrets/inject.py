"""Secret inject resolve + redaction helpers.

Scope: resolve_for_tool (call-local env dict), resolve_access_token_for_tool
(OAuth access-only allowlist hook for future host builtins), redact helpers.
Never merges into guest/host-stub env. Never invents inject_class meta fields.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from elyra.secrets.policy import (
    REDACT_PLACEHOLDER,
    SECRET_WRITE_ARG_KEYS,
    TOOL_SECRET_REQUIREMENTS,
    env_var_for_secret,
)
from elyra.secrets.store import SecretsStore

# Future host-builtin allowlist for OAuth access inject (PR6 / KD20).
# Access-only — never refresh_token. Not wired into registry secret_env yet;
# no grok_build tool package in this PR.
GROK_BUILD_TOOL_NAMES: frozenset[str] = frozenset({"grok_build"})


def resolve_for_tool(tool_name: str, store: SecretsStore) -> dict[str, str]:
    """Build call-local ``secret_env`` for a tool (may be empty).

    Does **not** raise for missing secrets or grants (registry soft path).
    Only injects a secret when the tool is listed in that secret's grants.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return {}
    required = TOOL_SECRET_REQUIREMENTS.get(tool_name) or []
    if not required:
        return {}
    env: dict[str, str] = {}
    for secret_name in required:
        meta = store.get_meta(secret_name)
        if meta is None:
            continue
        grants = meta.get("grants") or []
        if not isinstance(grants, list):
            continue
        # Grant match is case-sensitive on stored tool names (normalize-friendly:
        # accept casefold equality so operators can paste mixed case).
        granted = any(
            isinstance(g, str) and g.casefold() == tool_name.casefold() for g in grants
        )
        if not granted:
            continue
        value = store.get_value(secret_name)
        if value is None or value == "":
            continue
        env_var = env_var_for_secret(secret_name)
        env[env_var] = value
        try:
            store.touch_last_used(secret_name)
        except Exception:  # noqa: BLE001 — never fail inject on meta touch
            pass
    return env


def resolve_access_token_for_tool(tool_name: str, data_dir: Path) -> str | None:
    """Return a fresh OAuth **access** token for an allowlisted tool, or None.

    Access-only: never returns ``refresh_token``. Guest/host-stub paths must not
    merge this into process env via ``secret_env`` (registry guest scrub still
    ignores ``secret_env``). Fail-closed on missing/reauth/refresh failure.

    No ``inject_class`` meta; allowlist is code-only (``GROK_BUILD_TOOL_NAMES``).
    """
    if not isinstance(tool_name, str) or not tool_name:
        return None
    if tool_name not in GROK_BUILD_TOOL_NAMES:
        return None
    try:
        from elyra.llm.xai_oauth import ensure_fresh_access
    except Exception:  # noqa: BLE001 — fail closed if oauth layer unavailable
        return None
    try:
        result = ensure_fresh_access(Path(data_dir))
    except Exception:  # noqa: BLE001 — never raise into tool dispatch
        return None
    if not result.ok:
        return None
    access = result.access_token
    if not isinstance(access, str) or not access.strip():
        return None
    return access.strip()


def redact_tool_call_arguments(
    name: str,
    args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a shallow-copied args dict with secret write keys set to ``***``.

    Pure helper for chain serialization of SECRET_WRITE_TOOLS.
    """
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    for key in list(out.keys()):
        if isinstance(key, str) and key in SECRET_WRITE_ARG_KEYS:
            out[key] = REDACT_PLACEHOLDER
    return out


def redact_string(text: str, known_values: list[str] | tuple[str, ...] | None) -> str:
    """Replace known secret substrings in text with the redaction placeholder."""
    if not text or not known_values:
        return text
    out = text
    # Longest first to avoid partial overlap artifacts.
    for val in sorted((v for v in known_values if v), key=len, reverse=True):
        if val and val in out:
            out = out.replace(val, REDACT_PLACEHOLDER)
    return out


def redact_payload(
    payload: Any,
    known_values: list[str] | tuple[str, ...] | None,
) -> Any:
    """Recursively redact known secret values from a JSON-like payload."""
    if not known_values:
        return payload
    if isinstance(payload, str):
        return redact_string(payload, known_values)
    if isinstance(payload, dict):
        return {k: redact_payload(v, known_values) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(v, known_values) for v in payload]
    if isinstance(payload, tuple):
        return tuple(redact_payload(v, known_values) for v in payload)
    return payload


def redact_tool_result_payload(
    payload: dict[str, Any] | None,
    known_values: list[str] | tuple[str, ...] | None,
) -> dict[str, Any]:
    """Redact a ToolResult.payload dict (always returns a dict)."""
    if not isinstance(payload, dict):
        return {}
    redacted = redact_payload(copy.deepcopy(payload), known_values)
    return redacted if isinstance(redacted, dict) else {}


__all__ = [
    "GROK_BUILD_TOOL_NAMES",
    "redact_payload",
    "redact_string",
    "redact_tool_call_arguments",
    "redact_tool_result_payload",
    "resolve_access_token_for_tool",
    "resolve_for_tool",
]
