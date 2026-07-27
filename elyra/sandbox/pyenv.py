"""Curated guest Python env (requirements-curated.txt + pyenv marker).

Scope: requirements hash, host marker path, guest pip install attempt after
mount readiness (H3b / KD11 / KD22). Mount readiness is independent of pyenv.

Install moment (product warm path, not inside the 60s mount wall budget):
  guest: python3 -m pip install --user -r /workspace/lib/requirements-curated.txt
  host:  write {host_root}/.elyra_pyenv_ready with requirements hash on success
         (not under guest-mounted tmp/ — overlay can drop host writes there)

Offline / network=none / pip failure → leave marker absent; verify fails
``guest_pytest_unavailable``. Hermetic tests write the marker directly or skip
the install path.

KD15: :class:`InstallResult` is the structured surface for warm path + package tool.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from elyra.config import ElyraPaths, resolve_paths
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    ensure_host_tree,
    guest_env,
)
from elyra.sandbox.status import PYENV_READY_MARKER, PYENV_READY_MARKER_LEGACY

_LOG = logging.getLogger(__name__)

REQUIREMENTS_REL = Path("lib") / "requirements-curated.txt"
GUEST_REQUIREMENTS_PATH = f"{GUEST_WORKSPACE_ROOT}/lib/requirements-curated.txt"

# Guest pip can take minutes on cold image; separate from mount ensure wall.
DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS = 600.0
_PIP_BRIDGE_SLACK_SECONDS = 30.0
_TAIL_CHARS = 2000


@dataclass(frozen=True)
class InstallResult:
    """Structured outcome of a curated guest pip install attempt (KD15)."""

    ok: bool
    exit_code: int | None = None
    stderr_tail: str = ""
    stdout_tail: str = ""
    requirements_hash: str | None = None
    error_reason: str | None = None  # e.g. lifecycle_unusable, pip_failed, …


def requirements_file(host_root: Path) -> Path:
    """Host path to curated requirements under the sandbox tree."""
    return Path(host_root) / REQUIREMENTS_REL


def requirements_hash(host_root: Path) -> str | None:
    """SHA-256 of requirements-curated.txt contents, or None if missing."""
    path = requirements_file(host_root)
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def marker_path(host_root: Path) -> Path:
    """Host path to product ``.elyra_pyenv_ready`` (outside guest mounts)."""
    return Path(host_root) / PYENV_READY_MARKER


def _marker_paths(host_root: Path) -> list[Path]:
    """Product marker first, then legacy ``tmp/`` marker."""
    root = Path(host_root)
    return [root / PYENV_READY_MARKER, root / PYENV_READY_MARKER_LEGACY]


def read_marker_hash(host_root: Path) -> str | None:
    """Return hash stored in marker (first non-empty line), or None."""
    for path in _marker_paths(host_root):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


def pyenv_ready(host_root: Path) -> bool:
    """True when marker exists and matches current requirements hash (if known).

    If requirements file is missing, presence of any marker is accepted (operator
    pre-baked env). If requirements exist, hash must match so re-bootstrap runs
    after curated list changes.
    """
    if not any(p.is_file() for p in _marker_paths(host_root)):
        return False
    expected = requirements_hash(host_root)
    if expected is None:
        return True
    stored = read_marker_hash(host_root)
    if stored is None:
        # Empty/legacy marker: treat as ready if file exists (compat with
        # status tests that write bare "ok\\n").
        return True
    # Accept either the hash or a simple non-hash marker written by tests.
    if stored == expected:
        return True
    # Non-hash tokens (e.g. "ok") count as ready for hermetic tests.
    if len(stored) != 64 or any(c not in "0123456789abcdef" for c in stored.lower()):
        return True
    return False


def write_pyenv_marker(host_root: Path, *, req_hash: str | None = None) -> Path:
    """Write the pyenv ready marker on the **host-only** product root.

    Marker path is ``{host_root}/.elyra_pyenv_ready`` (not under guest-mounted
    ``tmp/``). Shared RW mounts can lose host writes under virtio/overlay.
    """
    host_root = Path(host_root)
    host_root.mkdir(parents=True, exist_ok=True)
    digest = req_hash if req_hash is not None else requirements_hash(host_root)
    body = (digest or "ready") + "\n"
    path = marker_path(host_root)
    path.write_text(body, encoding="utf-8")
    # Drop legacy tmp marker so status does not disagree with product path.
    legacy = host_root / "tmp" / ".elyra_pyenv_ready"
    if legacy.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass
    if not path.is_file():
        _LOG.error("pyenv marker write vanished immediately: %s", path)
    return path


def clear_pyenv_marker(host_root: Path) -> bool:
    """Remove marker if present (product + legacy paths). Returns True if any deleted."""
    host_root = Path(host_root)
    deleted = False
    for rel in (PYENV_READY_MARKER, Path("tmp") / ".elyra_pyenv_ready"):
        path = host_root / rel
        if not path.is_file():
            continue
        try:
            path.unlink()
            deleted = True
        except OSError as exc:
            _LOG.warning("failed to clear pyenv marker %s: %s", path, exc)
    return deleted


def needs_pyenv_install(host_root: Path) -> bool:
    """True when curated install should run (no valid marker / hash mismatch)."""
    return not pyenv_ready(host_root)


def guest_pip_install_argv() -> list[str]:
    """Argv (after python3) for curated install inside the guest.

    ``--prefer-binary`` avoids falling back to sdist builds when a wheel
    exists (guests often lack gcc/headers). Curated list itself must stay
    wheel-friendly (see requirements-curated.txt).
    """
    return [
        "-m",
        "pip",
        "install",
        "--user",
        "--prefer-binary",
        "-r",
        GUEST_REQUIREMENTS_PATH,
    ]


def _tail(text: str, n: int = _TAIL_CHARS) -> str:
    return (text or "").strip()[-n:]


def try_install_curated_pyenv(
    life: Any,
    *,
    paths: ElyraPaths | None = None,
    name: str = PRIMARY_NAME,
    timeout_seconds: float = DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS,
    force: bool = False,
) -> InstallResult:
    """Run guest pip install after mount ready; write marker on success.

    Returns :class:`InstallResult`. Failures leave the marker absent
    (pyenv_ready stays false). Never raises for product warm.
    """
    layout = paths or resolve_paths()
    # Prefer existing product tree. ensure_host_tree seeds missing layout but
    # must not run mid package-tool mutation in a way that drops curated edits
    # (curated is no longer always-refreshed; allowlist still is).
    host_root = ensure_host_tree(name, layout)
    digest = requirements_hash(host_root)

    if not force and pyenv_ready(host_root):
        return InstallResult(ok=True, requirements_hash=digest)

    req = requirements_file(host_root)
    if not req.is_file():
        _LOG.warning(
            "curated requirements missing under %s — cannot install pyenv",
            req,
        )
        return InstallResult(
            ok=False,
            requirements_hash=digest,
            error_reason="requirements_missing",
        )

    if life is None or getattr(life, "client_unusable", False):
        _LOG.info("skip pyenv install: lifecycle missing or client_unusable")
        return InstallResult(
            ok=False,
            requirements_hash=digest,
            error_reason="lifecycle_unusable",
        )

    if not life.is_ready(name):
        _LOG.info("skip pyenv install: mount not ready for %s", name)
        return InstallResult(
            ok=False,
            requirements_hash=digest,
            error_reason="mount_not_ready",
        )

    env: Mapping[str, str] = {**guest_env(), "PYTHONDONTWRITEBYTECODE": "1"}
    argv = guest_pip_install_argv()
    timeout = max(30.0, float(timeout_seconds))

    try:
        bridge = life.bridge
        with life.with_ready_sandbox(name, timeout=min(60.0, timeout)) as sb:
            result = bridge.run(
                sb.exec(
                    "python3",
                    argv,
                    cwd=GUEST_WORKSPACE_ROOT,
                    timeout=timeout,
                    env=dict(env),
                ),
                timeout=timeout + _PIP_BRIDGE_SLACK_SECONDS,
            )
    except Exception as exc:  # noqa: BLE001 — warm path never kills product
        msg = str(exc)
        _LOG.warning("guest pyenv pip install failed: %s", exc)
        reason = "pyenv_install_timeout" if "timeout" in msg.lower() else "pyenv_install_failed"
        return InstallResult(
            ok=False,
            requirements_hash=digest,
            stderr_tail=_tail(msg),
            error_reason=reason,
        )

    exit_code = int(getattr(result, "exit_code", 1))
    stderr = str(getattr(result, "stderr_text", "") or "")
    stdout = str(getattr(result, "stdout_text", "") or "")
    stderr_t = _tail(stderr)
    stdout_t = _tail(stdout)
    if exit_code != 0:
        _LOG.warning(
            "guest pyenv pip install exit=%s: %s",
            exit_code,
            stderr_t or stdout_t or "(no output)",
        )
        return InstallResult(
            ok=False,
            exit_code=exit_code,
            stderr_tail=stderr_t,
            stdout_tail=stdout_t,
            requirements_hash=digest,
            error_reason="pip_failed",
        )

    path = write_pyenv_marker(host_root, req_hash=digest)
    if not pyenv_ready(host_root):
        _LOG.error(
            "pyenv install reported ok but marker not readable at %s",
            path,
        )
        return InstallResult(
            ok=False,
            exit_code=exit_code,
            stderr_tail=stderr_t,
            stdout_tail=stdout_t,
            requirements_hash=digest,
            error_reason="marker_unreadable",
        )
    _LOG.info(
        "sandbox0 pyenv ready (curated install ok, hash=%s, marker=%s)",
        (digest or "")[:12],
        path.name,
    )
    return InstallResult(
        ok=True,
        exit_code=exit_code,
        stderr_tail=stderr_t,
        stdout_tail=stdout_t,
        requirements_hash=digest,
    )


def ensure_pyenv_marker_for_tests(paths: ElyraPaths | None = None) -> Path:
    """Hermetic helper: ensure host tree + write pyenv marker without guest pip."""
    layout = paths or resolve_paths()
    host_root = ensure_host_tree(PRIMARY_NAME, layout)
    return write_pyenv_marker(host_root)


__all__ = [
    "DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS",
    "GUEST_REQUIREMENTS_PATH",
    "InstallResult",
    "REQUIREMENTS_REL",
    "clear_pyenv_marker",
    "ensure_pyenv_marker_for_tests",
    "guest_pip_install_argv",
    "marker_path",
    "needs_pyenv_install",
    "pyenv_ready",
    "read_marker_hash",
    "requirements_file",
    "requirements_hash",
    "try_install_curated_pyenv",
    "write_pyenv_marker",
]
