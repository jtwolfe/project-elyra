"""Pure argv / prompt-body construction for grok_build headless CLI.

Scope: pure mapping of mode + validated args → argv list + prompt body.
In scope: slash prefixes, execute_plan flags inside -p, human-gate policy text,
          artifact output path suffix, base=working instructions.
Out of scope: subprocess, auth, filesystem, registry.
CRITICAL: PE ``effort`` int goes ONLY inside the -p prompt string
  (e.g. "/implement --effort 2 …"). NEVER pass CLI --effort / --reasoning-effort
  from that integer (those are none|minimal|low|medium|high|…).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from elyra.instrument.modes import (
    DEFAULT_BASE_BRANCH,
    HUMAN_GATE_MODES,
    Mode,
    SLASH_PREFIX,
)

# KD15 — injected for design / implement / execute_plan / review.
HUMAN_GATE_POLICY: str = """HEADLESS PE POLICY (mandatory):
- You are running non-interactively for Project Elyra. There is no human at this TTY.
- Do NOT block waiting for interactive clarification, ask_user_question, or permission prompts.
- If you need a human decision: write remaining open questions into the designated
  artifact under the PE output directory, set a clear NEEDS_HUMAN section, and end the run.
- Prefer fail-closed documented gaps over inventing product decisions.
- Do not spin escalate loops beyond 2 rounds of unresolved needs-user-input; then NEEDS_HUMAN stop."""

# execute_plan BASE_AND_POLICY always injected (plus optional args.instructions).
EXECUTE_PLAN_BASE_AND_POLICY: str = (
    f"Stack bottom base branch MUST be '{DEFAULT_BASE_BRANCH}' (not main). Host skill defaults "
    "that say main are overridden. If working is missing, fail clearly. "
    "Prefer short-lived execute-plan/* branches. Do not force-push main/working. "
    "Stale stacks >10 days behind working: restack or extend with reason. "
    "On human-needed conflicts or ambiguous stack decisions: write needs_human "
    "notes into the run summary and stop — do not hang on interactive ask tools."
)

# Artifact path suffixes (KD17) — prefer run_dir/artifacts/.
ARTIFACT_DESIGN_DOC = "design.md"
ARTIFACT_DESIGN_SUMMARY = "summary.md"
ARTIFACT_REVIEW = "review.md"


def _nonempty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def artifact_path_suffix(mode: Mode, artifacts_dir: str | Path | None) -> str:
    """Return prompt suffix directing Grok to PE-controlled artifact paths."""
    if artifacts_dir is None:
        return ""
    root = Path(artifacts_dir)
    if mode is Mode.DESIGN:
        design = root / ARTIFACT_DESIGN_DOC
        summary = root / ARTIFACT_DESIGN_SUMMARY
        return (
            f"Write the final design document to: {design}\n"
            f"Write a short design summary to: {summary}"
        )
    if mode is Mode.REVIEW:
        review = root / ARTIFACT_REVIEW
        return f"Write the final review document to: {review}"
    if mode is Mode.IMPLEMENT:
        return (
            f"If you produce review notes or design residuals, write them under: {root}/"
        )
    if mode is Mode.EXECUTE_PLAN:
        return (
            f"Write run summary / needs_human notes under: {root}/ "
            f"(e.g. summary.md)."
        )
    return ""


def review_slash_target(target: str | None) -> str:
    """Map review ``target`` arg to skill flags inside the -p body.

    | target arg        | slash form              |
    |-------------------|-------------------------|
    | omitted / local   | /review --local         |
    | branch name       | /review --branch <name> |
    | PR number or URL  | /review --pr <id-or-url>|
    """
    t = _nonempty_str(target)
    if t is None or t.casefold() == "local":
        return f"{SLASH_PREFIX[Mode.REVIEW]} --local"
    # PR number (digits) or URL containing github PR path / pull/
    if t.isdigit() or "github.com" in t.casefold() or "/pull/" in t.casefold() or t.casefold().startswith("pr/"):
        return f"{SLASH_PREFIX[Mode.REVIEW]} --pr {t}"
    # Otherwise treat as branch name
    return f"{SLASH_PREFIX[Mode.REVIEW]} --branch {t}"


def build_slash_prompt(
    mode: Mode | str,
    *,
    prompt: str | None = None,
    design_doc_path: str | None = None,
    effort: int | None = None,
    concurrency: int | None = None,
    target: str | None = None,
    use_graphite: bool = False,
    auto_pr: bool = False,
    resume_id: str | None = None,
    instructions: str | None = None,
    artifacts_dir: str | Path | None = None,
    extra_skill_flags: list[str] | None = None,
) -> str:
    """Build the headless ``-p`` prompt body (slash skill + policy + paths).

    Skill flags (effort, --no-graphite, review targets) live **only** here —
    never as bare CLI argv tokens.
    """
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    if m is None:
        raise ValueError(f"unknown mode: {mode!r}")

    parts: list[str] = []

    if m is Mode.PROMPT:
        body = _nonempty_str(prompt) or ""
        parts.append(body)
    elif m is Mode.DESIGN:
        head = SLASH_PREFIX[m]
        text = _nonempty_str(prompt) or ""
        parts.append(f"{head} {text}".rstrip() if text else head)
    elif m is Mode.IMPLEMENT:
        head = SLASH_PREFIX[m]
        flags: list[str] = []
        if effort is not None:
            flags.append(f"--effort {int(effort)}")
        if extra_skill_flags:
            flags.extend(extra_skill_flags)
        text = _nonempty_str(prompt) or ""
        mid = " ".join(flags)
        if mid and text:
            parts.append(f"{head} {mid} {text}")
        elif mid:
            parts.append(f"{head} {mid}")
        elif text:
            parts.append(f"{head} {text}")
        else:
            parts.append(head)
        if instructions:
            parts.append(f"Additional instructions: {instructions.strip()}")
    elif m is Mode.EXECUTE_PLAN:
        head = SLASH_PREFIX[m]
        path = _nonempty_str(design_doc_path) or ""
        flags = []
        if not use_graphite:
            flags.append("--no-graphite")
        if auto_pr:
            flags.append("--auto-pr")
        if effort is not None:
            flags.append(f"--effort {int(effort)}")
        if concurrency is not None:
            flags.append(f"--concurrency {int(concurrency)}")
        if resume_id:
            flags.append(f"--resume {resume_id.strip()}")
        if extra_skill_flags:
            flags.extend(extra_skill_flags)
        # Path then skill flags then --instructions
        policy = EXECUTE_PLAN_BASE_AND_POLICY
        extra = _nonempty_str(instructions)
        if extra:
            policy = f"{policy} {extra}"
        flag_s = " ".join(flags)
        core = f"{head} {path}".rstrip()
        if flag_s:
            core = f"{core} {flag_s}"
        core = f'{core} --instructions "{policy}"'
        parts.append(core)
    elif m is Mode.DEEP_RESEARCH:
        head = SLASH_PREFIX[m]
        text = _nonempty_str(prompt) or ""
        parts.append(f"{head} {text}".rstrip() if text else head)
    elif m is Mode.REVIEW:
        head = review_slash_target(target)
        text = _nonempty_str(prompt)
        if text:
            parts.append(f"{head} {text}")
        else:
            parts.append(head)
    else:  # pragma: no cover — enum exhaustive
        raise ValueError(f"unhandled mode: {m}")

    # Human-gate policy (KD15)
    if m in HUMAN_GATE_MODES:
        parts.append(HUMAN_GATE_POLICY)

    # Artifact harvest path suffix (KD17)
    suffix = artifact_path_suffix(m, artifacts_dir)
    if suffix:
        parts.append(suffix)

    return "\n\n".join(p for p in parts if p)


def build_cli_argv(
    prompt_body: str,
    *,
    cwd: str | Path | None = None,
    always_approve: bool = True,
    model: str | None = None,
    max_turns: int | None = None,
    output_format: str = "json",
    prompt_file: str | Path | None = None,
    grok_bin: str = "grok",
) -> list[str]:
    """Build subprocess argv tokens (CLI flags only — no skill effort flags).

    PE ``effort`` must never appear here as ``--effort`` / ``--reasoning-effort``.
    """
    argv: list[str] = [grok_bin]
    if prompt_file is not None:
        argv.extend(["--prompt-file", str(prompt_file)])
    else:
        argv.extend(["-p", prompt_body])
    if output_format:
        argv.extend(["--output-format", output_format])
    if always_approve:
        argv.append("--always-approve")
    if cwd is not None:
        argv.extend(["--cwd", str(cwd)])
    if model:
        argv.extend(["-m", str(model)])
    if max_turns is not None:
        argv.extend(["--max-turns", str(int(max_turns))])
    return argv


def build_argv_for_mode(
    mode: Mode | str,
    *,
    prompt: str | None = None,
    design_doc_path: str | None = None,
    effort: int | None = None,
    concurrency: int | None = None,
    target: str | None = None,
    use_graphite: bool = False,
    auto_pr: bool = False,
    resume_id: str | None = None,
    instructions: str | None = None,
    artifacts_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    always_approve: bool = True,
    model: str | None = None,
    max_turns: int | None = None,
    output_format: str = "json",
    grok_bin: str = "grok",
    extra_skill_flags: list[str] | None = None,
) -> tuple[list[str], str]:
    """Convenience: return ``(cli_argv, prompt_body)`` for a mode call."""
    body = build_slash_prompt(
        mode,
        prompt=prompt,
        design_doc_path=design_doc_path,
        effort=effort,
        concurrency=concurrency,
        target=target,
        use_graphite=use_graphite,
        auto_pr=auto_pr,
        resume_id=resume_id,
        instructions=instructions,
        artifacts_dir=artifacts_dir,
        extra_skill_flags=extra_skill_flags,
    )
    argv = build_cli_argv(
        body,
        cwd=cwd,
        always_approve=always_approve,
        model=model,
        max_turns=max_turns,
        output_format=output_format,
        grok_bin=grok_bin,
    )
    return argv, body


__all__ = [
    "ARTIFACT_DESIGN_DOC",
    "ARTIFACT_DESIGN_SUMMARY",
    "ARTIFACT_REVIEW",
    "EXECUTE_PLAN_BASE_AND_POLICY",
    "HUMAN_GATE_POLICY",
    "artifact_path_suffix",
    "build_argv_for_mode",
    "build_cli_argv",
    "build_slash_prompt",
    "review_slash_target",
]
