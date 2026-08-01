"""ToolResult-shaped helpers and pure artifact harvest for grok_build.

Scope: normalize success/failure dicts; harvest algorithm pure functions
(parse paths from text, scan patterns, prefer run_dir/artifacts/).
In scope: path regexes, copy plan (no mandatory I/O), NEEDS_HUMAN parse,
          status mapping helpers.
Out of scope: subprocess, OAuth, usage metering, job reaper, live TMP scans
              that require network — FS ops are optional via call-supplied paths.

Harvest order (KD17) — stop at first success for primary artifact:
  1. Prompt-directed path under run_dir/artifacts/ (preferred)
  2. Parse stdout/JSON text for grok-*-*.md absolute paths
  3. Scratch scan fallback (caller supplies candidate files)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from elyra.instrument.argv import (
    ARTIFACT_DESIGN_DOC,
    ARTIFACT_DESIGN_SUMMARY,
    ARTIFACT_REVIEW,
)
from elyra.instrument.modes import ARTIFACT_REQUIRED_MODES, Mode

# Known Grok TMP / stdout artifact filename patterns (KD17).
_PATH_GLOBS = (
    r"grok-design-doc-[^\s\"']+\.md",
    r"grok-design-summary-[^\s\"']+\.md",
    r"grok-review-[^\s\"']+\.md",
    r"grok-execute-plan-[^\s\"']+",
)

# Absolute or home-ish path prefix + known basename.
_ARTIFACT_PATH_RE = re.compile(
    r"(?P<path>(?:/|(?:[A-Za-z]:\\)|~/)[^\s\"']*(?:"
    + "|".join(_PATH_GLOBS)
    + r"))",
    re.IGNORECASE,
)

_NEEDS_HUMAN_RE = re.compile(
    r"(?im)^\s*#{0,3}\s*NEEDS_HUMAN\b.*?(?=^\s*#{0,3}\s+\S|\Z)",
    re.DOTALL,
)
_OPEN_QUESTION_LINE_RE = re.compile(
    r"(?m)^\s*(?:[-*]|\d+\.)\s+(.+\S)\s*$",
)

STATUS_COMPLETED = "completed"
STATUS_RUNNING = "running"
STATUS_NEEDS_HUMAN = "needs_human"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"


def make_success_payload(
    *,
    mode: str | Mode,
    run_id: str | None = None,
    status: str = STATUS_COMPLETED,
    summary: str = "",
    open_questions: Sequence[str] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    session_id: str | None = None,
    usage: Mapping[str, Any] | None = None,
    exit_code: int | None = 0,
    job_id: str | None = None,
    log_path: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a model-visible success payload (ToolResult.payload shape)."""
    m = mode.value if isinstance(mode, Mode) else str(mode)
    payload: dict[str, Any] = {
        "ok": True,
        "mode": m,
        "run_id": run_id,
        "status": status,
        "summary": summary or "",
        "open_questions": list(open_questions or []),
        "artifacts": [dict(a) for a in (artifacts or [])],
        "session_id": session_id,
        "usage": dict(usage) if usage else {"total_tokens": 0, "recorded": False},
        "exit_code": exit_code,
        "job_id": job_id,
        "log_path": log_path,
    }
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return payload


def make_error_payload(
    error_reason: str,
    *,
    mode: str | Mode | None = None,
    summary: str = "",
    run_id: str | None = None,
    job_id: str | None = None,
    hint: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a failure payload; pair with ToolResult(ok=False, error_reason=…)."""
    payload: dict[str, Any] = {
        "ok": False,
        "error_reason": error_reason,
        "summary": summary or "",
        "run_id": run_id,
        "job_id": job_id,
    }
    if mode is not None:
        payload["mode"] = mode.value if isinstance(mode, Mode) else str(mode)
    if hint:
        payload["hint"] = hint
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return payload


def tool_result_dict(
    *,
    ok: bool,
    payload: Mapping[str, Any] | None = None,
    error_reason: str | None = None,
) -> dict[str, Any]:
    """ToolResult-shaped dict (not the dataclass) for pure tests / serialization."""
    return {
        "ok": ok,
        "payload": dict(payload or {}),
        "error_reason": error_reason,
    }


def parse_artifact_paths_from_text(text: str | None) -> list[str]:
    """Extract absolute-looking grok artifact paths from stdout / JSON text."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _ARTIFACT_PATH_RE.finditer(text):
        p = m.group("path").rstrip(".,);]'\"")
        if p not in seen:
            seen.add(p)
            found.append(p)
    return found


def parse_needs_human(text: str | None) -> tuple[bool, list[str]]:
    """Detect NEEDS_HUMAN section; return (found, open_questions)."""
    if not text:
        return False, []
    if not re.search(r"(?i)\bNEEDS_HUMAN\b", text):
        return False, []
    questions: list[str] = []
    for block in _NEEDS_HUMAN_RE.finditer(text):
        for line in _OPEN_QUESTION_LINE_RE.finditer(block.group(0)):
            q = line.group(1).strip()
            if q and q not in questions:
                questions.append(q)
    # If section present but no bullets, still mark needs_human.
    return True, questions


def preferred_artifact_names(mode: Mode | str) -> list[str]:
    """Stable names under run_dir/artifacts/ for the mode."""
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    if m is Mode.DESIGN:
        return [ARTIFACT_DESIGN_DOC, ARTIFACT_DESIGN_SUMMARY]
    if m is Mode.REVIEW:
        return [ARTIFACT_REVIEW]
    if m is Mode.EXECUTE_PLAN:
        return ["summary.md"]
    return []


def collect_prompt_directed_artifacts(
    artifacts_dir: str | Path | None,
    mode: Mode | str,
) -> list[dict[str, Any]]:
    """Strategy (1): files already under run_dir/artifacts/ with size > 0."""
    if artifacts_dir is None:
        return []
    root = Path(artifacts_dir)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    names = preferred_artifact_names(m) if m else []
    for name in names:
        path = root / name
        try:
            if path.is_file() and path.stat().st_size > 0:
                kind = _kind_for_name(name, m)
                out.append({"kind": kind, "path": str(path), "source": "prompt_directed"})
        except OSError:
            continue
    return out


def _kind_for_name(name: str, mode: Mode | None) -> str:
    if name == ARTIFACT_DESIGN_DOC:
        return "design_doc"
    if name == ARTIFACT_DESIGN_SUMMARY:
        return "design_summary"
    if name == ARTIFACT_REVIEW:
        return "review"
    if name == "summary.md":
        return "summary"
    if mode is Mode.DESIGN:
        return "design_doc"
    if mode is Mode.REVIEW:
        return "review"
    return "artifact"


def plan_copies_from_parsed_paths(
    paths: Sequence[str],
    artifacts_dir: str | Path,
    mode: Mode | str,
) -> list[dict[str, Any]]:
    """Strategy (2): map parsed absolute paths → stable names under artifacts_dir.

    Pure plan: does not copy; returns {kind, source_path, dest_path, source}.
    """
    root = Path(artifacts_dir)
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    plans: list[dict[str, Any]] = []
    used_dest: set[str] = set()
    for raw in paths:
        src = Path(raw)
        name_l = src.name.lower()
        if "design-doc" in name_l or name_l == ARTIFACT_DESIGN_DOC:
            dest_name = ARTIFACT_DESIGN_DOC
            kind = "design_doc"
        elif "design-summary" in name_l or name_l == ARTIFACT_DESIGN_SUMMARY:
            dest_name = ARTIFACT_DESIGN_SUMMARY
            kind = "design_summary"
        elif "review" in name_l:
            dest_name = ARTIFACT_REVIEW
            kind = "review"
        else:
            dest_name = src.name
            kind = _kind_for_name(dest_name, m)
        dest = root / dest_name
        key = str(dest)
        if key in used_dest:
            continue
        used_dest.add(key)
        plans.append(
            {
                "kind": kind,
                "source_path": str(src),
                "dest_path": key,
                "source": "parsed_stdout",
            }
        )
    return plans


def plan_copies_from_scratch(
    candidates: Iterable[str | Path],
    artifacts_dir: str | Path,
    mode: Mode | str,
    *,
    newer_than: float | None = None,
) -> list[dict[str, Any]]:
    """Strategy (3): from caller-supplied scratch candidates (newest preferred).

    ``candidates`` are absolute paths already filtered by the caller (or tests).
    Optional ``newer_than`` is a unix mtime floor when stat is available.
    """
    root = Path(artifacts_dir)
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    scored: list[tuple[float, Path]] = []
    for c in candidates:
        p = Path(c)
        try:
            if not p.is_file():
                continue
            st = p.stat()
            if st.st_size <= 0:
                continue
            if newer_than is not None and st.st_mtime < newer_than:
                continue
            scored.append((st.st_mtime, p))
        except OSError:
            continue
    scored.sort(key=lambda t: t[0], reverse=True)

    # Prefer one design-doc, one summary, one review (newest each).
    want = {
        "design_doc": ARTIFACT_DESIGN_DOC,
        "design_summary": ARTIFACT_DESIGN_SUMMARY,
        "review": ARTIFACT_REVIEW,
    }
    found: dict[str, Path] = {}
    for _, p in scored:
        name_l = p.name.lower()
        if "design-doc" in name_l and "design_doc" not in found:
            found["design_doc"] = p
        elif "design-summary" in name_l and "design_summary" not in found:
            found["design_summary"] = p
        elif "review" in name_l and "review" not in found:
            found["review"] = p

    # Mode filter: only relevant kinds
    if m is Mode.DESIGN:
        keys = ["design_doc", "design_summary"]
    elif m is Mode.REVIEW:
        keys = ["review"]
    else:
        keys = list(found.keys())

    plans: list[dict[str, Any]] = []
    for key in keys:
        src = found.get(key)
        if src is None:
            continue
        dest_name = want[key]
        plans.append(
            {
                "kind": key,
                "source_path": str(src),
                "dest_path": str(root / dest_name),
                "source": "scratch_scan",
            }
        )
    return plans


def harvest_artifacts(
    *,
    mode: Mode | str,
    artifacts_dir: str | Path | None,
    stdout_text: str | None = None,
    scratch_candidates: Sequence[str | Path] | None = None,
    run_start_mtime: float | None = None,
    apply_copies: bool = False,
) -> dict[str, Any]:
    """Run harvest strategies in order; return structured harvest result.

    Returns::
        {
          "artifacts": [{"kind", "path", "source"}, ...],
          "copy_plans": [...],  # strategies 2/3 not yet applied or applied
          "primary_found": bool,
          "error_reason": None | "artifact_missing",
          "needs_human": bool,
          "open_questions": [...],
        }

    When ``apply_copies`` is True, copy source→dest for plans (best-effort).
    Pure tests leave it False and inspect plans.
    """
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    needs_human, open_qs = parse_needs_human(stdout_text)

    artifacts: list[dict[str, Any]] = []
    copy_plans: list[dict[str, Any]] = []

    # Strategy 1 — preferred
    if artifacts_dir is not None and m is not None:
        directed = collect_prompt_directed_artifacts(artifacts_dir, m)
        if directed:
            artifacts.extend(directed)

    primary_found = _has_primary(artifacts, m)

    # Strategy 2 — parse paths
    if not primary_found and artifacts_dir is not None and m is not None:
        paths = parse_artifact_paths_from_text(stdout_text)
        plans = plan_copies_from_parsed_paths(paths, artifacts_dir, m)
        copy_plans.extend(plans)
        if apply_copies:
            artifacts.extend(_apply_plans(plans))
        else:
            # Surface planned dests as logical artifacts for inspection
            for plan in plans:
                artifacts.append(
                    {
                        "kind": plan["kind"],
                        "path": plan["dest_path"],
                        "source": plan["source"],
                        "source_path": plan["source_path"],
                        "pending_copy": True,
                    }
                )
        primary_found = _has_primary(artifacts, m)

    # Strategy 3 — scratch
    if (
        not primary_found
        and artifacts_dir is not None
        and m is not None
        and scratch_candidates
    ):
        plans = plan_copies_from_scratch(
            scratch_candidates,
            artifacts_dir,
            m,
            newer_than=run_start_mtime,
        )
        copy_plans.extend(plans)
        if apply_copies:
            artifacts.extend(_apply_plans(plans))
        else:
            for plan in plans:
                artifacts.append(
                    {
                        "kind": plan["kind"],
                        "path": plan["dest_path"],
                        "source": plan["source"],
                        "source_path": plan["source_path"],
                        "pending_copy": True,
                    }
                )
        primary_found = _has_primary(artifacts, m)

    error_reason: str | None = None
    if m is not None and m in ARTIFACT_REQUIRED_MODES and not primary_found:
        if needs_human:
            # design says needs_human if NEEDS_HUMAN without file
            error_reason = None
        else:
            error_reason = "artifact_missing"

    return {
        "artifacts": artifacts,
        "copy_plans": copy_plans,
        "primary_found": primary_found,
        "error_reason": error_reason,
        "needs_human": needs_human,
        "open_questions": open_qs,
    }


def _has_primary(artifacts: Sequence[Mapping[str, Any]], mode: Mode | None) -> bool:
    if mode is None:
        return bool(artifacts)
    if mode is Mode.DESIGN:
        return any(a.get("kind") == "design_doc" for a in artifacts)
    if mode is Mode.REVIEW:
        return any(a.get("kind") == "review" for a in artifacts)
    return bool(artifacts)


def _apply_plans(plans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    import shutil

    out: list[dict[str, Any]] = []
    for plan in plans:
        src = Path(str(plan["source_path"]))
        dest = Path(str(plan["dest_path"]))
        try:
            if not src.is_file() or src.stat().st_size <= 0:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            out.append(
                {
                    "kind": plan["kind"],
                    "path": str(dest),
                    "source": plan["source"],
                }
            )
        except OSError:
            continue
    return out


def resolve_status_from_harvest(
    harvest: Mapping[str, Any],
    *,
    exit_code: int | None = 0,
) -> str:
    """Map harvest + exit to ToolResult status string."""
    if harvest.get("needs_human"):
        return STATUS_NEEDS_HUMAN
    if harvest.get("error_reason") == "artifact_missing":
        return STATUS_FAILED
    if exit_code not in (0, None):
        return STATUS_FAILED
    return STATUS_COMPLETED


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_NEEDS_HUMAN",
    "STATUS_RUNNING",
    "collect_prompt_directed_artifacts",
    "harvest_artifacts",
    "make_error_payload",
    "make_success_payload",
    "parse_artifact_paths_from_text",
    "parse_needs_human",
    "plan_copies_from_parsed_paths",
    "plan_copies_from_scratch",
    "preferred_artifact_names",
    "resolve_status_from_harvest",
    "tool_result_dict",
]
