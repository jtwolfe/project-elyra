"""Sandbox path jail + MSB host/guest path constants.

Scope:
- Path jail: join + resolve under a fixed root; deny escapes and symlink escapes.
- MSB constants: guest mount map, primary name, network policy, host-tree ensure.
- ``isolation_enabled()`` / ``ELYRA_SANDBOX`` product isolation flag.

In scope: relative/absolute user paths, symlink target re-check, empty reject,
guest constants + host tree ensure for lifecycle, isolation flag.
Out of scope: FS I/O beyond ensure, process execution, hard-link inode isolation,
O_NOFOLLOW open races (callers may re-resolve before open).

Known limitations (path jail, not a mount namespace):
- Hard links created inside the root to outside inodes (same UID) resolve
  *under* root and are not detected as escapes. Symlinks are checked.

Product FS root is ``sandboxes/sandbox0`` (H2c cutover). Legacy
``data/sandbox/`` is cleared on reset but no longer used for FS tools.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths

# ---------------------------------------------------------------------------
# Path jail (product FS tools — root is caller's Sandbox root)
# ---------------------------------------------------------------------------


class PathEscapeError(ValueError):
    """Raised when a user path or symlink target escapes the sandbox root."""


def resolve(root: Path, user_path: str) -> Path:
    """Resolve ``user_path`` under ``root``; raise PathEscapeError if outside jail.

    Algorithm (persistent sandbox jail):
    - Reject empty / whitespace-only paths (use ``"."`` for root).
    - Join and resolve; deny if not under root.
    - Reject absolute paths that escape.
    - If the path is a symlink, re-check the resolved target under root.
    """
    if not isinstance(user_path, str):
        raise TypeError(f"user_path must be str, got {type(user_path).__name__}")
    # "." is the sandbox root; empty/whitespace is not a useful path.
    if user_path != "." and not user_path.strip():
        raise ValueError("path must be non-empty")

    root_r = root.resolve()
    raw = Path(user_path)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root_r / user_path).resolve()

    try:
        candidate.relative_to(root_r)
    except ValueError as exc:
        raise PathEscapeError(f"path escapes sandbox: {user_path!r}") from exc

    # Symlink re-check: after resolve the candidate may already be the target;
    # also inspect the pre-resolve path when it is a symlink (e.g. dangling).
    link = _symlink_path(root_r, user_path, candidate)
    if link is not None:
        target = link.resolve()
        try:
            target.relative_to(root_r)
        except ValueError as exc:
            raise PathEscapeError(
                f"symlink escapes sandbox: {user_path!r}"
            ) from exc

    return candidate


def _symlink_path(root_r: Path, user_path: str, candidate: Path) -> Path | None:
    """Return a path that is a symlink if one should be re-checked, else None."""
    if candidate.is_symlink():
        return candidate
    raw = Path(user_path)
    if raw.is_absolute():
        joined = raw
    else:
        joined = root_r / user_path
    # Avoid following: is_symlink is False for missing paths.
    try:
        if joined.is_symlink():
            return joined
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# MSB / host-tree constants (H2a; lifecycle consumes these in PR2)
# ---------------------------------------------------------------------------

# Guest mount root (fixed v1). Keep in sync with harness design.
GUEST_WORKSPACE_ROOT = "/workspace"
GUEST_ENV_SANDBOX_ROOT = "ELYRA_SANDBOX_ROOT"
PRIMARY_NAME = "sandbox0"

# Mount map: guest path → (host relative under primary root, readonly).
MOUNT_SPEC: tuple[tuple[str, str, bool], ...] = (
    (f"{GUEST_WORKSPACE_ROOT}/lib", "lib", True),
    (f"{GUEST_WORKSPACE_ROOT}/general", "general", True),
    (f"{GUEST_WORKSPACE_ROOT}/fixtures", "fixtures", True),
    (f"{GUEST_WORKSPACE_ROOT}/tmp", "tmp", False),
    (f"{GUEST_WORKSPACE_ROOT}/tools", "tools", False),
)

# Pinned create image / resource policy (SDK contract).
MSB_IMAGE = "python"
MSB_CPUS = 1
MSB_MEMORY_MIB = 512
MSB_SECURITY = "restricted"
MSB_PULL_POLICY = "if-missing"
# Network is create-time. Valid ids map 1:1 to microsandbox.Network factories:
# none | public_only | allow_all. Override with ELYRA_SANDBOX_NETWORK.
# Default public_only: outbound internet for tool dogfood; set
# ELYRA_SANDBOX_NETWORK=none for air-gapped posture.
_MSB_NETWORK_POLICIES = frozenset({"none", "public_only", "allow_all"})
_MSB_NETWORK_DEFAULT = "public_only"
# Backward-compat alias — prefer resolve_msb_network_policy_id() at call sites.
MSB_NETWORK_POLICY_ID = _MSB_NETWORK_DEFAULT

_PRIMARY_ALWAYS_DIRS = ("lib", "general", "fixtures", "tmp", "tools")


def resolve_msb_network_policy_id() -> str:
    """Return active microsandbox network policy id (env + default)."""
    raw = (os.environ.get("ELYRA_SANDBOX_NETWORK") or _MSB_NETWORK_DEFAULT).strip().lower()
    if raw in _MSB_NETWORK_POLICIES:
        return raw
    return _MSB_NETWORK_DEFAULT


# Opt-in isolation flag (single env: ELYRA_SANDBOX only — DESIGN).
ENV_ELYRA_SANDBOX = "ELYRA_SANDBOX"


def isolation_enabled() -> bool:
    """Return whether warm microsandbox isolation is enabled.

    Default is **on** when ``ELYRA_SANDBOX`` is unset (operator product default).
    Explicit off: ``0`` / ``false`` / ``no`` / ``off``.
    Explicit on: ``1`` / ``true`` / ``yes`` / ``on`` (or any other non-empty
    value that is not a recognized false token).

    Tests set ``ELYRA_SANDBOX=0`` for hermetic host-stub runs (PR4+).
    """
    raw = os.environ.get(ENV_ELYRA_SANDBOX)
    if raw is None:
        return True
    value = raw.strip().lower()
    if value in {"", "0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    # Unknown non-empty values: treat as enabled (fail-closed isolation path).
    return True


def host_root_for(
    name: str = PRIMARY_NAME,
    paths: ElyraPaths | None = None,
) -> Path:
    """Return host directory for a named sandbox instance."""
    layout = paths or resolve_paths()
    if name == PRIMARY_NAME:
        # Lazy: keep seed helpers in workspace_seed (avoid circular pressure).
        from elyra.sandbox.workspace_seed import host_primary_root

        return host_primary_root(layout)
    return layout.home / "sandboxes" / name


def ensure_host_tree(
    name: str = PRIMARY_NAME,
    paths: ElyraPaths | None = None,
    *,
    seed_source: Path | None = None,
) -> Path:
    """Ensure host tree exists with seed + chmod policy; return resolved root."""
    layout = paths or resolve_paths()
    if name == PRIMARY_NAME:
        from elyra.sandbox.workspace_seed import ensure_primary_sandbox_tree

        return ensure_primary_sandbox_tree(layout, seed_source=seed_source)
    # Future multi-sandbox seam: scaffold dirs only.
    root = host_root_for(name, layout)
    root.mkdir(parents=True, exist_ok=True)
    for d in _PRIMARY_ALWAYS_DIRS:
        (root / d).mkdir(exist_ok=True)
    return root.resolve()


def mount_fingerprint(
    name: str,
    host_root: Path,
    *,
    image: str = MSB_IMAGE,
    network_policy_id: str | None = None,
) -> str:
    """Stable hash of create-time mount policy + host roots (DESIGN fingerprint)."""
    if network_policy_id is None:
        network_policy_id = resolve_msb_network_policy_id()
    payload: dict[str, Any] = {
        "name": name,
        "image": image,
        "network_policy_id": network_policy_id,
        "host_root": str(host_root.resolve()),
        "mounts": [
            {
                "guest": guest,
                "host": str((host_root / host_rel).resolve()),
                "readonly": readonly,
            }
            for guest, host_rel, readonly in MOUNT_SPEC
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def guest_env() -> dict[str, str]:
    """Env vars injected into the guest at create and tool exec."""
    return {
        GUEST_ENV_SANDBOX_ROOT: GUEST_WORKSPACE_ROOT,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
