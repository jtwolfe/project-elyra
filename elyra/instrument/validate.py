"""Mode-conditional argument validation for grok_build (pure).

Scope: mode + args table → error_reason string or None (ok).
In scope: enum check, missing_prompt, invalid_effort, design_doc path presence,
          job_id poll XOR, deep_research experimental, long-mode readiness flags.
Out of scope: subprocess (git preflight), OAuth, registry dispatch, path jail
              roots (handler supplies jail outcomes as flags when needed).

Filesystem: optional existence check for design_doc_path only (no network).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from elyra.instrument.modes import (
    DEEP_RESEARCH_EXPERIMENTAL,
    Mode,
    defaults_async,
)

# Error reasons referenced by the normative validation table / common catalog.
ERROR_INVALID_ARGS = "invalid_args"
ERROR_MISSING_PROMPT = "missing_prompt"
ERROR_INVALID_EFFORT = "invalid_effort"
ERROR_MISSING_DESIGN_DOC_PATH = "missing_design_doc_path"
ERROR_DESIGN_DOC_MISSING = "design_doc_missing"
ERROR_BASE_BRANCH_MISSING = "base_branch_missing"
ERROR_MODE_EXPERIMENTAL = "mode_experimental"
ERROR_MODE_NOT_READY = "mode_not_ready"
ERROR_TARGET_AMBIGUOUS = "target_ambiguous"
ERROR_MISSING_REPO = "missing_repo"
ERROR_USAGE_HARD_STOP = "usage_hard_stop"


def _nonempty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _effort_valid(value: Any, *, lo: int, hi: int) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        return lo <= value <= hi
    # reject floats / strings that are not plain ints
    return False


def is_poll_only(args: Mapping[str, Any]) -> bool:
    """True when ``job_id`` is set — poll path, ignore spawn field requirements."""
    return _nonempty_str(args.get("job_id")) is not None


def validate_grok_build_args(
    args: Mapping[str, Any] | None,
    *,
    design_doc_exists: bool | None = None,
    base_branch_ok: bool | None = None,
    jobs_ready: bool | None = None,
    deep_research_enabled: bool | None = None,
    usage_allowed: bool | None = None,
    repo_resolved: bool | None = None,
    check_design_doc_fs: bool = True,
) -> str | None:
    """Validate mode-conditional args; return ``error_reason`` or None if ok.

    Optional flags let the thin handler inject preflight outcomes without
    this module owning git/subprocess or full path-jail logic:

    - ``design_doc_exists``: override FS check (None → check path if enabled)
    - ``base_branch_ok``: execute_plan working preflight (None → skip)
    - ``jobs_ready``: long modes need reaper (None → skip; False → mode_not_ready)
    - ``deep_research_enabled``: override experimental gate
    - ``usage_allowed``: meter pre-check (False → usage_hard_stop)
    - ``repo_resolved``: False → missing_repo
    - ``check_design_doc_fs``: when True and design_doc_exists is None, use Path.is_file()
    """
    if not isinstance(args, Mapping):
        return ERROR_INVALID_ARGS

    # Poll path: job_id set → poll only (prefer over spawn validation).
    job_id = _nonempty_str(args.get("job_id"))
    mode_raw = args.get("mode")
    mode = Mode.parse(mode_raw) if mode_raw is not None else None

    if job_id is not None:
        # Poll only: mode optional; reject unclear XOR only when spawn-primary
        # fields are present *without* a resolvable mode intent — design says
        # prefer: if job_id set, poll only. So poll always wins.
        return None

    # Spawn path requires mode ∈ enum
    if mode is None:
        return ERROR_INVALID_ARGS

    if usage_allowed is False:
        return ERROR_USAGE_HARD_STOP

    if repo_resolved is False:
        return ERROR_MISSING_REPO

    # Long modes before PR3: jobs/reaper required when caller says not ready.
    if jobs_ready is False and defaults_async(mode):
        return ERROR_MODE_NOT_READY

    # deep_research experimental until enabled
    if mode is Mode.DEEP_RESEARCH:
        enabled = (
            deep_research_enabled
            if deep_research_enabled is not None
            else (not DEEP_RESEARCH_EXPERIMENTAL)
        )
        if not enabled:
            return ERROR_MODE_EXPERIMENTAL
        if _nonempty_str(args.get("prompt")) is None:
            return ERROR_MISSING_PROMPT
        return None

    if mode is Mode.PROMPT:
        if _nonempty_str(args.get("prompt")) is None:
            return ERROR_MISSING_PROMPT
        return None

    if mode is Mode.DESIGN:
        if _nonempty_str(args.get("prompt")) is None:
            return ERROR_MISSING_PROMPT
        if jobs_ready is False:
            return ERROR_MODE_NOT_READY
        return None

    if mode is Mode.IMPLEMENT:
        if _nonempty_str(args.get("prompt")) is None:
            return ERROR_MISSING_PROMPT
        if not _effort_valid(args.get("effort"), lo=1, hi=5):
            return ERROR_INVALID_EFFORT
        return None

    if mode is Mode.EXECUTE_PLAN:
        path = _nonempty_str(args.get("design_doc_path"))
        if path is None:
            return ERROR_MISSING_DESIGN_DOC_PATH
        # Existence: explicit flag wins; else optional FS check.
        exists = design_doc_exists
        if exists is None and check_design_doc_fs:
            try:
                exists = Path(path).is_file()
            except OSError:
                exists = False
        if exists is False:
            return ERROR_DESIGN_DOC_MISSING
        if base_branch_ok is False:
            return ERROR_BASE_BRANCH_MISSING
        # effort for execute_plan is 1–2 when set (schema sketch); tolerate 1–5 soft
        effort = args.get("effort")
        if effort is not None and not _effort_valid(effort, lo=1, hi=5):
            return ERROR_INVALID_EFFORT
        concurrency = args.get("concurrency")
        if concurrency is not None and not _effort_valid(concurrency, lo=1, hi=8):
            return ERROR_INVALID_ARGS
        return None

    if mode is Mode.REVIEW:
        target = args.get("target")
        if target is not None and target != "" and not isinstance(target, str):
            return ERROR_TARGET_AMBIGUOUS
        if isinstance(target, str) and target.strip() == "":
            return ERROR_TARGET_AMBIGUOUS
        # Ambiguous: whitespace-only already caught; multi-token nonsense is soft.
        # If target has both PR-looking and branch-looking markers mixed oddly, soft.
        return None

    return ERROR_INVALID_ARGS


__all__ = [
    "ERROR_BASE_BRANCH_MISSING",
    "ERROR_DESIGN_DOC_MISSING",
    "ERROR_INVALID_ARGS",
    "ERROR_INVALID_EFFORT",
    "ERROR_MISSING_DESIGN_DOC_PATH",
    "ERROR_MISSING_PROMPT",
    "ERROR_MISSING_REPO",
    "ERROR_MODE_EXPERIMENTAL",
    "ERROR_MODE_NOT_READY",
    "ERROR_TARGET_AMBIGUOUS",
    "ERROR_USAGE_HARD_STOP",
    "is_poll_only",
    "validate_grok_build_args",
]
