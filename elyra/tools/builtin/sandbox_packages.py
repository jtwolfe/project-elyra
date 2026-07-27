"""Host builtin: sandbox_pip_update — allowlist-add guest package tool.

KD11: allowlist-add only (no free pip, no set_file body rewrite).
KD12: REQUIRED_CURATED (at least pytest) cannot be removed.
KD15: uses InstallResult from pyenv helpers.
KD6: host file snapshot/restore on install failure; guest_site_may_be_dirty
     honesty (never overclaim clean user-site after partial pip).

Scope: mutate host requirements-curated.txt under sandboxes/sandbox0/lib/,
clear marker, re-warm guest pip. Fail closed when isolation off or network none.
Out of scope: free-form PyPI, URL/VCS requirements, full uninstall guarantees.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from elyra.sandbox.paths import (
    PRIMARY_NAME,
    ensure_host_tree,
    isolation_enabled,
    resolve_msb_network_policy_id,
)
from elyra.sandbox.pyenv import (
    DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS,
    clear_pyenv_marker,
    pyenv_ready,
    requirements_file,
    requirements_hash,
    try_install_curated_pyenv,
)
from elyra.sandbox.registry import get_sandbox_lifecycle
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

# KD12 — isolation-on verify_tool depends on guest pytest.
REQUIRED_CURATED: frozenset[str] = frozenset({"pytest"})

ALLOWLIST_REL = Path("lib") / "requirements-allowlist.txt"
BACKUP_DIR_NAME = ".elyra_pyenv_backup"
BACKUP_REQ_NAME = "requirements-curated.txt.bak"

MAX_PACKAGES_PER_CALL = 10
MAX_REQUIREMENTS_BYTES = 64 * 1024

# Guest pip was entered (or install path reached guest); user-site may be dirty.
_GUEST_SITE_DIRTY_REASONS = frozenset(
    {
        "pip_failed",
        "pyenv_install_timeout",
        "pyenv_install_failed",
        "marker_unreadable",
    }
)

# Fail-closed: no URL/VCS/path/shell injection in requirement lines.
_UNSAFE_IN_REQ = re.compile(
    r"(://|@|\s--|^\s*-e\b|/|\\|\||&|\$|`|;|\n|\r|\[|\]|\(|\))",
    re.IGNORECASE,
)
# name + optional PEP 440-ish version specs (no env markers / extras / URLs).
_REQ_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?P<spec>"
    r"(?:\s*(?:===|==|!=|<=|>=|~=|<|>)\s*"
    r"[A-Za-z0-9_.*+!-]+"
    r"(?:\s*,\s*(?:===|==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9_.*+!-]+)*)?"
    r")$"
)

# Fallback allowlist if seed file missing (same names as seed; documented).
_FALLBACK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "requests",
        "httpx",
        "beautifulsoup4",
        "pyyaml",
        "python-dateutil",
        "regex",
        "jinja2",
        "packaging",
        "markdown",
        "charset-normalizer",
        "idna",
        "urllib3",
        "sniffio",
        "anyio",
        "certifi",
        "soupsieve",
        "markupsafe",
        "tabulate",
        "tenacity",
        "toml",
        "tomli",
        "typing-extensions",
        "click",
        "rich",
    }
)

_INSTALL_FAIL_HINT = (
    "Host requirements restored; marker cleared. Guest user-site may still "
    "contain partially installed wheels. Retry with wheel-friendly pins, or "
    "recreate sandbox0 if imports look wrong."
)
_REMOVE_DIRTY_HINT = (
    "Requirements bookkeeping + re-warm succeeded; removed packages may still "
    "import from guest user-site until sandbox recreate or explicit uninstall."
)
_ALLOWLIST_HINT = (
    "Only packages on lib/requirements-allowlist.txt may be added. "
    "Ask the operator to extend the allowlist."
)


def normalize_dist_name(name: str) -> str:
    """PEP 503 normalize: lowercase, collapse [-_.] runs to single hyphen."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def allowlist_file(host_root: Path) -> Path:
    return Path(host_root) / ALLOWLIST_REL


def load_allowlist(host_root: Path) -> frozenset[str]:
    """Load allowlisted distribution names (normalized). Prefer host seed file."""
    path = allowlist_file(host_root)
    if not path.is_file():
        _LOG.warning(
            "requirements-allowlist.txt missing under %s — using fallback frozenset",
            path,
        )
        return frozenset(normalize_dist_name(n) for n in _FALLBACK_ALLOWLIST)
    names: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("failed to read allowlist %s: %s — using fallback", path, exc)
        return frozenset(normalize_dist_name(n) for n in _FALLBACK_ALLOWLIST)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Allowlist is names only; strip accidental pins.
        name = re.split(r"[<>=!~\s]", stripped, maxsplit=1)[0].strip()
        if name:
            names.add(normalize_dist_name(name))
    if not names:
        return frozenset(normalize_dist_name(n) for n in _FALLBACK_ALLOWLIST)
    return frozenset(names)


def parse_requirement_line(raw: str) -> tuple[str, str] | None:
    """Parse a package token into (normalized_name, full_requirement_line).

    Returns None when the line is unsafe or malformed (fail-closed).
    """
    if not isinstance(raw, str):
        return None
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if _UNSAFE_IN_REQ.search(line):
        return None
    # Collapse internal whitespace around operators for match.
    compact = re.sub(r"\s+", "", line) if re.search(r"[<>=!~]", line) else line
    # Prefer original spacing-free form for storage when pins present.
    m = _REQ_LINE_RE.match(compact if re.search(r"[<>=!~]", line) else line)
    if not m:
        return None
    name = m.group("name")
    spec = m.group("spec") or ""
    if re.search(r"[<>=!~]", line):
        # Re-match original with optional spaces already stripped via compact.
        req_line = f"{name}{spec}" if spec else name
    else:
        req_line = name
    return normalize_dist_name(name), req_line


def _parse_curated_entries(text: str) -> list[tuple[str | None, str]]:
    """Return list of (normalized_name_or_None, original_line) preserving order."""
    out: list[tuple[str | None, str]] = []
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\n\r")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out.append((None, raw))
            continue
        parsed = parse_requirement_line(stripped)
        if parsed is None:
            # Keep opaque lines (comments already handled); treat unknown as keep.
            out.append((None, raw))
            continue
        out.append((parsed[0], raw))
    return out


def _entries_to_text(entries: list[tuple[str | None, str]]) -> str:
    lines: list[str] = []
    for _name, raw in entries:
        lines.append(raw.rstrip("\n\r"))
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def _curated_names(entries: list[tuple[str | None, str]]) -> set[str]:
    return {n for n, _ in entries if n is not None}


def _required_present(entries: list[tuple[str | None, str]]) -> set[str]:
    present = _curated_names(entries)
    return {r for r in REQUIRED_CURATED if r not in present}


def _err(
    reason: str,
    *,
    action: str | None = None,
    packages: list[str] | None = None,
    host_reverted: bool = False,
    guest_site_may_be_dirty: bool = False,
    hint: str | None = None,
    detail: str | None = None,
    **extra: Any,
) -> ToolResult:
    payload: dict[str, Any] = {
        "ok": False,
        "error_reason": reason,
        "host_reverted": host_reverted,
        "guest_site_may_be_dirty": guest_site_may_be_dirty,
    }
    if action is not None:
        payload["action"] = action
    if packages is not None:
        payload["packages"] = packages
    if hint is not None:
        payload["hint"] = hint
    if detail is not None:
        payload["detail"] = detail
    payload.update(extra)
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _backup_dir(host_root: Path) -> Path:
    return Path(host_root) / BACKUP_DIR_NAME


def _snapshot_requirements(host_root: Path, text: str | None) -> Path | None:
    """Write host-only backup of requirements text. Returns backup path or None."""
    if text is None:
        return None
    bdir = _backup_dir(host_root)
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        path = bdir / BACKUP_REQ_NAME
        path.write_text(text, encoding="utf-8")
        return path
    except OSError as exc:
        _LOG.warning("failed to write pyenv backup under %s: %s", bdir, exc)
        return None


def _restore_requirements(host_root: Path, snapshot: str | None) -> bool:
    """Restore requirements-curated.txt from in-memory snapshot. True if restored."""
    req = requirements_file(host_root)
    try:
        if snapshot is None:
            if req.is_file():
                req.unlink()
            return True
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text(snapshot, encoding="utf-8")
        return True
    except OSError as exc:
        _LOG.error("failed to restore requirements snapshot: %s", exc)
        return False


def _merge_add(
    entries: list[tuple[str | None, str]],
    additions: list[tuple[str, str]],
) -> list[tuple[str | None, str]]:
    """Merge allowlisted requirement lines into curated entries (replace by name)."""
    by_name = {name: req for name, req in additions}
    seen: set[str] = set()
    out: list[tuple[str | None, str]] = []
    for name, raw in entries:
        if name is not None and name in by_name:
            out.append((name, by_name[name]))
            seen.add(name)
        else:
            out.append((name, raw))
    for name, req in additions:
        if name not in seen:
            out.append((name, req))
            seen.add(name)
    return out


def _apply_remove(
    entries: list[tuple[str | None, str]],
    remove_names: set[str],
) -> list[tuple[str | None, str]]:
    return [
        (name, raw)
        for name, raw in entries
        if name is None or name not in remove_names
    ]


def _validate_packages_arg(
    raw: Any,
) -> tuple[list[str], str | None]:
    if not isinstance(raw, list):
        return [], "invalid_args"
    if len(raw) == 0:
        return [], "empty_packages"
    if len(raw) > MAX_PACKAGES_PER_CALL:
        return [], "packages_too_many"
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return [], "invalid_package_spec"
        out.append(item.strip())
    return out, None


def _parse_timeout_seconds(raw: Any) -> tuple[float | None, str | None]:
    """Validate timeout before any host mutation. Returns (seconds, error_reason)."""
    if raw is None:
        return float(DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS), None
    if isinstance(raw, bool):
        return None, "invalid_timeout"
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, "invalid_timeout"
        try:
            value = float(s)
        except ValueError:
            return None, "invalid_timeout"
    else:
        return None, "invalid_timeout"
    if value != value or value <= 0:  # NaN or non-positive
        return None, "invalid_timeout"
    # Clamp model-facing timeout to a sane range.
    return max(30.0, min(value, float(DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS))), None


def _map_install_error_reason(reason: str | None) -> str:
    if reason in {"pip_failed", "marker_unreadable", "pyenv_install_failed"}:
        return "pyenv_install_failed"
    if reason == "pyenv_install_timeout":
        return "pyenv_install_timeout"
    if reason == "lifecycle_unusable":
        return "lifecycle_unusable"
    if reason == "mount_not_ready":
        return "mount_not_ready"
    if reason == "requirements_missing":
        return "requirements_missing"
    return "pyenv_install_failed"


def sandbox_pip_update(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Allowlist-add or narrow-remove packages in guest curated requirements."""
    action_raw = args.get("action")
    action = action_raw.strip().lower() if isinstance(action_raw, str) else ""
    packages_raw, perr = _validate_packages_arg(args.get("packages"))
    if perr:
        return _err(
            perr,
            action=action or None,
            packages=packages_raw or None,
            hint=(
                f"Provide 1–{MAX_PACKAGES_PER_CALL} non-empty package strings."
                if perr in {"empty_packages", "packages_too_many", "invalid_args"}
                else "Each package must be a plain name or name+version pin."
            ),
        )

    if action not in {"add", "remove"}:
        # Reject set_file and anything else (v1).
        return _err(
            "invalid_action",
            action=action_raw if isinstance(action_raw, str) else None,
            packages=packages_raw,
            hint="v1 supports action=add|remove only (no set_file).",
        )

    if not isolation_enabled():
        return _err(
            "isolation_required",
            action=action,
            packages=packages_raw,
            hint="sandbox_pip_update requires isolation on (ELYRA_SANDBOX≠0).",
        )

    network = resolve_msb_network_policy_id()
    if network == "none":
        return _err(
            "network_policy_blocks_pip",
            action=action,
            packages=packages_raw,
            hint="Guest network is none; pip install cannot reach indexes.",
        )

    # Validate timeout with other hard walls — never after host mutation (KD6).
    timeout, terr = _parse_timeout_seconds(args.get("timeout_seconds"))
    if terr is not None or timeout is None:
        return _err(
            "invalid_timeout",
            action=action,
            packages=packages_raw,
            hint=(
                "timeout_seconds must be a positive number "
                f"(clamped 30–{int(DEFAULT_PYENV_INSTALL_TIMEOUT_SECONDS)})."
            ),
        )

    host_root = ensure_host_tree(PRIMARY_NAME, ctx.paths)
    allowlist = load_allowlist(host_root)
    req_path = requirements_file(host_root)

    parsed: list[tuple[str, str]] = []
    for token in packages_raw:
        item = parse_requirement_line(token)
        if item is None:
            return _err(
                "invalid_package_spec",
                action=action,
                packages=packages_raw,
                hint=(
                    "Reject URL/VCS/path/shell-looking specs. "
                    "Use allowlisted names or name+pin (e.g. httpx>=0.27,<1)."
                ),
                detail=token,
            )
        parsed.append(item)

    # Hard walls: REQUIRED_CURATED first (remove), then allowlist.
    for name, _req in parsed:
        if action == "remove" and name in REQUIRED_CURATED:
            return _err(
                "missing_required_package",
                action=action,
                packages=packages_raw,
                hint=(
                    f"Required curated package {name!r} cannot be removed "
                    f"(REQUIRED_CURATED={sorted(REQUIRED_CURATED)})."
                ),
                detail=name,
            )
        if name not in allowlist:
            return _err(
                "package_not_allowlisted",
                action=action,
                packages=packages_raw,
                hint=_ALLOWLIST_HINT,
                detail=name,
            )

    if not req_path.is_file():
        return _err(
            "requirements_missing",
            action=action,
            packages=packages_raw,
            hint="lib/requirements-curated.txt missing under sandbox host tree.",
        )

    try:
        original = req_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _err(
            "requirements_unreadable",
            action=action,
            packages=packages_raw,
            detail=str(exc),
        )

    entries = _parse_curated_entries(original)
    if action == "add":
        new_entries = _merge_add(entries, parsed)
    else:
        remove_set = {n for n, _ in parsed}
        new_entries = _apply_remove(entries, remove_set)

    missing_req = _required_present(new_entries)
    if missing_req:
        return _err(
            "missing_required_package",
            action=action,
            packages=packages_raw,
            hint=(
                f"Resulting requirements would omit required package(s): "
                f"{sorted(missing_req)}. REQUIRED_CURATED cannot be dropped."
            ),
        )

    new_text = _entries_to_text(new_entries)
    if len(new_text.encode("utf-8")) > MAX_REQUIREMENTS_BYTES:
        return _err(
            "requirements_too_large",
            action=action,
            packages=packages_raw,
            hint=f"requirements-curated.txt max {MAX_REQUIREMENTS_BYTES} bytes.",
        )

    package_names = [n for n, _ in parsed]

    # No-op: file unchanged — success without re-warm.
    if new_text == original:
        digest = requirements_hash(host_root)
        dirty = action == "remove"
        payload: dict[str, Any] = {
            "ok": True,
            "action": action,
            "packages": package_names,
            "requirements_hash": digest,
            "pyenv_ready": pyenv_ready(host_root),
            "host_reverted": False,
            "guest_site_may_be_dirty": dirty,
            "unchanged": True,
        }
        if dirty:
            payload["hint"] = _REMOVE_DIRTY_HINT
        return ToolResult(ok=True, payload=payload)

    # Snapshot before any mutation (in-memory + host-only backup dir).
    _snapshot_requirements(host_root, original)
    mutated = False
    try:
        try:
            req_path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return _err(
                "requirements_write_failed",
                action=action,
                packages=package_names,
                detail=str(exc),
            )
        mutated = True
        clear_pyenv_marker(host_root)
        life = get_sandbox_lifecycle()

        result = try_install_curated_pyenv(
            life,
            paths=ctx.paths,
            name=PRIMARY_NAME,
            timeout_seconds=timeout,
            force=True,
        )

        if result.ok and pyenv_ready(host_root):
            dirty = action == "remove"
            payload = {
                "ok": True,
                "action": action,
                "packages": package_names,
                "requirements_hash": result.requirements_hash
                or requirements_hash(host_root),
                "pyenv_ready": True,
                "host_reverted": False,
                "guest_site_may_be_dirty": dirty,
            }
            if dirty:
                payload["hint"] = _REMOVE_DIRTY_HINT
            return ToolResult(ok=True, payload=payload)

        # Failure path: restore host files, clear marker, honest dirty flag.
        restored = _restore_requirements(host_root, original)
        clear_pyenv_marker(host_root)
        reason = result.error_reason or "pyenv_install_failed"
        tool_reason = _map_install_error_reason(reason)
        guest_dirty = reason in _GUEST_SITE_DIRTY_REASONS
        detail = result.stderr_tail or result.stdout_tail or reason
        _LOG.warning(
            "sandbox_pip_update install failed action=%s reason=%s restored=%s",
            action,
            reason,
            restored,
        )
        return _err(
            tool_reason,
            action=action,
            packages=package_names,
            host_reverted=restored,
            guest_site_may_be_dirty=guest_dirty,
            detail=detail,
            hint=(
                _INSTALL_FAIL_HINT
                if guest_dirty
                else "Host requirements restored; marker cleared. Guest pip was not run."
            ),
            requirements_hash=requirements_hash(host_root),
            pyenv_ready=False,
            install_error_reason=result.error_reason,
            exit_code=result.exit_code,
        )
    except Exception as exc:  # noqa: BLE001 — KD6: always restore after mutation
        if mutated:
            restored = _restore_requirements(host_root, original)
            clear_pyenv_marker(host_root)
            _LOG.exception(
                "sandbox_pip_update unexpected error after mutation; restored=%s",
                restored,
            )
            return _err(
                "unexpected_error",
                action=action,
                packages=package_names,
                host_reverted=restored,
                # Unknown whether guest pip ran — prefer dirty honesty.
                guest_site_may_be_dirty=True,
                detail=str(exc),
                hint=_INSTALL_FAIL_HINT,
                requirements_hash=requirements_hash(host_root),
                pyenv_ready=False,
            )
        raise


__all__ = [
    "ALLOWLIST_REL",
    "MAX_PACKAGES_PER_CALL",
    "MAX_REQUIREMENTS_BYTES",
    "REQUIRED_CURATED",
    "allowlist_file",
    "load_allowlist",
    "normalize_dist_name",
    "parse_requirement_line",
    "sandbox_pip_update",
]
