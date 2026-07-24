"""Sandbox status block for GET /api/status (no secrets, no host paths).

Scope: operator-visible readiness — ready / warming / unusable + mount/pyenv.
In scope: allowlisted reason strings; network policy id; host_tree_exists bool.
Out of scope: full inspect HTTP routes, guest ps/FS browse (deferred past H2c).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.sandbox.paths import (
    PRIMARY_NAME,
    host_root_for,
    isolation_enabled,
    resolve_msb_network_policy_id,
)
from elyra.sandbox.registry import get_sandbox_lifecycle

# Host marker written after curated (+ pytest) guest install (H3b / KD22).
# MUST live *outside* guest RW mounts (tmp/tools) — virtio/overlay can drop
# host writes under mounted RW dirs; product root file is host-only.
PYENV_READY_MARKER = Path(".elyra_pyenv_ready")
# Legacy path (pre-fix); still accepted for one release so restarts stay warm.
PYENV_READY_MARKER_LEGACY = Path("tmp") / ".elyra_pyenv_ready"

# Coarse glass-pill states (KD27).
PILL_READY = "ready"
PILL_WARMING = "warming"
PILL_UNUSABLE = "unusable"
PILL_OFF = "off"

# Allowlisted reason tokens surfaced to operators (no host paths / secrets).
_REASON_ALLOWLIST = frozenset(
    {
        "warming",
        "client_unusable",
        "msb_not_installed",
        "ensure_wall_timeout",
        "lock_timeout",
        "create_failed",
        "mount_not_ready",
        "pyenv_not_ready",
        "degraded",
        "isolation_disabled",
        "host_not_ready",
        "get_failed",
        "ping_failed",
        "connect_failed",
        "start_failed",
        "drain_timeout",
        "fingerprint_mismatch",
        "crashed",
        "unknown_status",
        "readiness_error",
        "ensure_raised",
    }
)


def pyenv_ready_marker_present(host_root: Path) -> bool:
    """True when curated guest install marker exists under host tree."""
    root = Path(host_root)
    return (root / PYENV_READY_MARKER).is_file() or (
        root / PYENV_READY_MARKER_LEGACY
    ).is_file()


def _sanitize_reason(raw: str | None) -> str | None:
    """Map internal ensure reasons to allowlisted tokens (no secrets/paths)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text in _REASON_ALLOWLIST:
        return text
    # Prefixed reasons from lifecycle: create_failed:..., get_failed:..., etc.
    for prefix in (
        "create_failed",
        "get_failed",
        "ping_failed",
        "connect_failed",
        "start_failed",
        "readiness_error",
        "unknown_status",
        "ensure_not_ready",
    ):
        if text == prefix or text.startswith(f"{prefix}:"):
            return prefix if prefix in _REASON_ALLOWLIST else "degraded"
    if text in {"host_not_ready", "mount_not_ready"}:
        return text
    return "degraded"


def sandbox_status_block(
    paths: ElyraPaths | None = None,
    *,
    name: str = PRIMARY_NAME,
    warm_reason: str | None = None,
    warm_done: bool | None = None,
) -> dict[str, Any]:
    """Build the ``sandbox`` object for GET /api/status.

    Parameters
    ----------
    warm_reason:
        Optional supervisor override while async warm is in progress.
    warm_done:
        Optional supervisor override; when None, inferred from lifecycle
        ``ensure_attempted`` / ``client_unusable``.
    """
    layout = paths or resolve_paths()
    host_root = host_root_for(name, layout)
    host_tree_exists = host_root.is_dir()
    iso = isolation_enabled()
    life = get_sandbox_lifecycle()
    lifecycle_registered = life is not None
    client_unusable = bool(getattr(life, "client_unusable", False)) if life else False
    mount_ready = bool(life.is_ready(name)) if life is not None else False
    pyenv_ready = (
        pyenv_ready_marker_present(host_root) if host_tree_exists else False
    )
    ensure_done = False
    if warm_done is not None:
        ensure_done = warm_done
    elif life is not None:
        attempted = getattr(life, "ensure_attempted", None)
        if callable(attempted):
            ensure_done = bool(attempted(name))
        elif client_unusable:
            ensure_done = True

    # H3b: product ready == mount_ready && (pyenv_ready || isolation off).
    # Isolation-off is host-stub only — ready stays false (pill=off).
    ready = bool(mount_ready and (pyenv_ready or not iso))

    last_reason: str | None = None
    if life is not None:
        getter = getattr(life, "last_ensure_reason", None)
        if callable(getter):
            last_reason = getter(name)

    reason: str | None
    if not iso:
        reason = "isolation_disabled"
    elif client_unusable:
        reason = "client_unusable"
    elif mount_ready and pyenv_ready:
        reason = None
    elif mount_ready and not pyenv_ready:
        # Mount OK; curated env still installing or failed — not fully ready.
        reason = "pyenv_not_ready"
    elif warm_reason is not None and not ensure_done:
        reason = _sanitize_reason(warm_reason) or "warming"
    elif not ensure_done and lifecycle_registered and iso:
        reason = "warming"
    else:
        reason = _sanitize_reason(last_reason) or (
            _sanitize_reason(warm_reason) if warm_reason else "degraded"
        )

    # Coarse pill mapping (KD27). pyenv_not_ready after mount still "warming"
    # until curated install finishes (or stays unusable if mount never came up).
    if not iso:
        pill = PILL_OFF
    elif ready:
        pill = PILL_READY
    elif reason in {"warming", "pyenv_not_ready"}:
        pill = PILL_WARMING
    else:
        pill = PILL_UNUSABLE

    return {
        "isolation_enabled": iso,
        "name": name,
        "ready": ready,
        "mount_ready": mount_ready,
        "pyenv_ready": pyenv_ready,
        "lifecycle_registered": lifecycle_registered,
        "client_unusable": client_unusable,
        "reason": reason,
        "network_policy": resolve_msb_network_policy_id(),
        "host_tree_exists": host_tree_exists,
        "pill": pill,
    }
