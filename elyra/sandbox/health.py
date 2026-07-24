"""Sandbox health and mount readiness checks.

Scope: host pre-check + guest ping/readiness helpers used by ensure SM.
In scope: pure-ish functions over ConnectedSandbox + host Path.
Out of scope: ensure state machine, tool invoke.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from elyra.sandbox.paths import GUEST_WORKSPACE_ROOT

if TYPE_CHECKING:
    from elyra.sandbox.protocol import ConnectedSandbox

_LOG = logging.getLogger(__name__)

_ROOT = GUEST_WORKSPACE_ROOT  # fixed guest mount; keep probes aligned


def _guest_probes() -> list[tuple[str, str, list[str]]]:
    """Return (label, cmd, args) probes built from GUEST_WORKSPACE_ROOT."""
    now = f"{_ROOT}/general/now.py"
    tmp_probe = f"{_ROOT}/tmp/.elyra_ready"
    return [
        ("guest_python", "python3", ["-B", "-c", "print(1)"]),
        (
            "guest_ro_seed",
            "python3",
            [
                "-B",
                "-c",
                f"import pathlib; pathlib.Path({now!r}).read_text()",
            ],
        ),
        (
            "guest_ro_import",
            "python3",
            [
                "-B",
                "-c",
                (
                    "import importlib.util; "
                    f"p={now!r}; "
                    "s=importlib.util.spec_from_file_location('now', p); "
                    "m=importlib.util.module_from_spec(s); "
                    "assert s and s.loader; s.loader.exec_module(m)"
                ),
            ],
        ),
        (
            "guest_rw_tmp",
            "python3",
            [
                "-B",
                "-c",
                (
                    "from pathlib import Path; "
                    f"p=Path({tmp_probe!r}); "
                    "p.write_text('ok'); p.unlink()"
                ),
            ],
        ),
    ]


def host_seed_readable(host_root: Path) -> bool:
    """True when lib/ and general/now.py exist and are readable."""
    lib = host_root / "lib"
    now = host_root / "general" / "now.py"
    if not lib.is_dir() or not now.is_file():
        return False
    try:
        now.read_text(encoding="utf-8")
        return True
    except OSError:
        return False


def host_tmp_tools_writable(host_root: Path) -> bool:
    """True when tmp/ and tools/ exist and are writable by this process."""
    tmp = host_root / "tmp"
    tools = host_root / "tools"
    if not tmp.is_dir() or not tools.is_dir():
        return False
    try:
        probe = tmp / ".elyra_host_ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        marker = tools / ".elyra_host_ready"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def host_mount_ready(host_root: Path) -> tuple[bool, str | None]:
    """Host-side pre-check before guest readiness."""
    if not host_seed_readable(host_root):
        return False, "host_seed_not_readable"
    if not host_tmp_tools_writable(host_root):
        return False, "host_tmp_tools_not_writable"
    return True, None


async def ping_ok(sandbox: ConnectedSandbox) -> bool:
    """Return True when sandbox.ping() succeeds truthfully."""
    try:
        return bool(await sandbox.ping())
    except Exception as exc:  # noqa: BLE001 — operational probe
        _LOG.debug("sandbox ping failed for %s: %s", getattr(sandbox, "name", "?"), exc)
        return False


async def _exec_ok(
    sandbox: ConnectedSandbox,
    cmd: str,
    args: list[str],
    *,
    timeout: float = 15.0,
) -> bool:
    try:
        result = await sandbox.exec(
            cmd,
            args,
            cwd=GUEST_WORKSPACE_ROOT,
            timeout=timeout,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
        )
        return result.exit_code == 0
    except Exception as exc:  # noqa: BLE001 — operational probe
        _LOG.debug("guest readiness exec failed: %s %s — %s", cmd, args[:1], exc)
        return False


async def guest_mount_ready(
    sandbox: ConnectedSandbox,
    *,
    timeout: float = 15.0,
) -> tuple[bool, str | None]:
    """Guest mount readiness probes (DESIGN table)."""
    for label, cmd, args in _guest_probes():
        if not await _exec_ok(sandbox, cmd, args, timeout=timeout):
            return False, label
    return True, None


async def full_readiness(
    sandbox: ConnectedSandbox,
    host_root: Path,
    *,
    timeout: float = 15.0,
) -> tuple[bool, str | None]:
    """Host pre-check + guest probes. Pass → Ready eligible."""
    ok, reason = host_mount_ready(host_root)
    if not ok:
        return False, reason
    return await guest_mount_ready(sandbox, timeout=timeout)
