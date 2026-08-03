"""Host builtin: grok_build — thin broker for the Grok Build instrument.

Scope: validate → single OAuth mint → path jail → seed GROK_HOME (auth.json) →
sync run or async job spawn → ToolResult. Never assigns OAuth into secret_env.
In scope: mode-conditional args, execute_plan base-branch preflight, poll job_id.
Out of scope: reimplementing Grok skill loops; guest secret_env; god modules.

Auth: ensure_fresh_access once (KD-F13); seed access-only ExternalBinary
auth.json; mid-run mint via elyra.instrument.auth_provider. Never XAI_API_KEY
from OAuth; never put access in meta.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from elyra.config import project_root
from elyra.instrument.argv import build_argv_for_mode
from elyra.instrument.auth_handoff import seed_isolated_home
from elyra.instrument.discover import (
    GrokNotFoundError,
    GrokSkillsUnavailableError,
    find_grok_binary,
)
from elyra.instrument.jobs import (
    ARTIFACTS_DIR_NAME,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    RESULT_NAME,
    STDERR_NAME,
    STDOUT_NAME,
    create_job,
    is_pid_alive,
    load_job,
    load_result,
    reap_instrument_pid,
    run_dir_for,
    shred_tokens,
    update_job,
    update_job_status,
    write_result,
)
from elyra.instrument.modes import (
    DEEP_RESEARCH_EXPERIMENTAL,
    DEFAULT_BASE_BRANCH,
    MAX_TIMEOUT_S,
    Mode,
    default_timeout_s,
    defaults_async,
)
from elyra.instrument.process import run_grok, spawn_grok
from elyra.instrument.redact import merge_known_values, redact_result_payload
from elyra.instrument.reaper import auth_known_values_for_finalize, finalize_job
from elyra.instrument.result import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_HUMAN,
    STATUS_RUNNING,
    make_error_payload,
    make_success_payload,
)
from elyra.instrument.usage_bridge import meter_allows_call
from elyra.instrument.validate import is_poll_only, validate_grok_build_args
from elyra.llm.xai_oauth import ensure_fresh_access, expires_at_from_expires_in
from elyra.settings import Settings, default_settings
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.vcs_jail import (
    PathJailError,
    effective_allowed_roots,
    resolve_repo_path,
)

_LOG = logging.getLogger(__name__)

# Soft git preflight timeout (execute_plan base branch).
_GIT_PREFLIGHT_TIMEOUT_S = 15.0


def _err(
    reason: str,
    *,
    mode: str | Mode | None = None,
    hint: str | None = None,
    **extra: Any,
) -> ToolResult:
    payload = make_error_payload(reason, mode=mode, hint=hint, extra=extra or None)
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _settings(ctx: ToolContext) -> Settings:
    if ctx.settings is not None:
        return ctx.settings
    return default_settings()


def _meter_from_ctx(ctx: ToolContext) -> Any:
    """Optional UsageMeter from extras (supervisor / tests inject)."""
    if not isinstance(ctx.extras, dict):
        return None
    meter = ctx.extras.get("usage_meter") or ctx.extras.get("meter")
    return meter


def _want_async(mode: Mode, args: dict[str, Any]) -> bool:
    raw = args.get("async")
    if isinstance(raw, bool):
        return raw
    return defaults_async(mode)


def _resolve_cwd(
    args: dict[str, Any],
    ctx: ToolContext,
) -> Path | ToolResult:
    """Path jail resolve order: cwd → project_root if git under roots → missing_repo."""
    roots = effective_allowed_roots(_settings(ctx), ctx.paths)
    raw = args.get("cwd")
    if isinstance(raw, str) and raw.strip():
        try:
            return resolve_repo_path(raw.strip(), roots, require_git=True)
        except PathJailError as exc:
            return ToolResult(
                ok=False,
                payload=make_error_payload(
                    exc.reason,
                    hint=str(exc),
                    extra={"cwd": raw.strip()},
                ),
                error_reason=exc.reason,
            )

    # Default: project_root when under allowed roots and contains .git.
    try:
        root = project_root().resolve()
    except OSError:
        return _err("missing_repo", hint="no cwd and project_root unresolvable")
    under = False
    for r in roots:
        try:
            rr = Path(r).resolve()
            if root == rr or root.is_relative_to(rr):
                under = True
                break
        except OSError:
            continue
    if under and (root / ".git").exists():
        return root
    return _err(
        "missing_repo",
        hint="pass cwd to a git repo under allowed_repo_roots",
    )


def _jail_file_path(
    raw: str,
    ctx: ToolContext,
    *,
    base: Path | None = None,
) -> Path | ToolResult:
    """Resolve a non-repo file path under the VCS jail (design_doc_path)."""
    roots = effective_allowed_roots(_settings(ctx), ctx.paths)
    try:
        return resolve_repo_path(
            raw,
            roots,
            require_git=False,
            base=base,
        )
    except PathJailError as exc:
        return ToolResult(
            ok=False,
            payload=make_error_payload(
                exc.reason,
                hint=str(exc),
                extra={"path": raw},
            ),
            error_reason=exc.reason,
        )


def _base_branch_exists(repo: Path, branch: str) -> bool:
    """True if local or origin/<branch> ref exists (best-effort fetch skip)."""
    candidates = [branch, f"origin/{branch}"]
    for ref in candidates:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=_GIT_PREFLIGHT_TIMEOUT_S,
                check=False,
                shell=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return True
    return False


def _terminalize_orphan(
    ctx: ToolContext,
    job_id: str,
    *,
    mode: Mode | str | None,
    error_reason: str,
    hint: str | None = None,
) -> ToolResult:
    """Mark a post-create_job failure terminal so it is not left running forever."""
    run_dir = run_dir_for(ctx.paths, job_id)
    try:
        update_job_status(
            ctx.paths,
            job_id,
            JOB_STATUS_FAILED,
            error_reason=error_reason,
        )
    except FileNotFoundError:
        pass
    payload = make_error_payload(
        error_reason,
        mode=mode,
        hint=hint,
        job_id=job_id,
        run_id=job_id,
        extra={"status": STATUS_FAILED},
    )
    payload["status"] = STATUS_FAILED
    payload["ok"] = False
    try:
        write_result(ctx.paths, job_id, payload)
    except FileNotFoundError:
        pass
    shred_tokens(run_dir)
    return ToolResult(ok=False, payload=payload, error_reason=error_reason)


def _poll_job(job_id: str, ctx: ToolContext) -> ToolResult:
    meta = load_job(ctx.paths, job_id)
    if meta is None:
        return _err("job_not_found", hint=f"unknown job_id={job_id!r}")
    result = load_result(ctx.paths, job_id)
    if result is not None:
        ok = bool(result.get("ok", meta.status in (STATUS_COMPLETED, STATUS_NEEDS_HUMAN)))
        # needs_human is success path for PE.
        if result.get("status") == STATUS_NEEDS_HUMAN:
            ok = True
        err = None if ok else (result.get("error_reason") or meta.error_reason or "failed")
        return ToolResult(ok=ok, payload=dict(result), error_reason=err)

    # Opportunistic finalize when meta says running but pid is dead/zombie (KD-F6/F14).
    if meta.status == JOB_STATUS_RUNNING and meta.pid is not None:
        reaped = reap_instrument_pid(meta.pid)
        if reaped is not None or not is_pid_alive(meta.pid):
            code = reaped if reaped is not None else -1
            known = auth_known_values_for_finalize(ctx.paths)
            try:
                _meta, result = finalize_job(
                    ctx.paths,
                    job_id,
                    meter=_meter_from_ctx(ctx),
                    exit_code=code,
                    known_values=known,
                )
            except Exception as exc:  # noqa: BLE001 — never crash the tool
                _LOG.exception("grok_build poll finalize failed job_id=%s", job_id)
                return _err(
                    "skill_failed",
                    mode=meta.mode,
                    hint=f"finalize failed: {type(exc).__name__}",
                    job_id=job_id,
                )
            ok = bool(result.get("ok")) and str(result.get("status")) in (
                STATUS_COMPLETED,
                STATUS_NEEDS_HUMAN,
            )
            if result.get("status") == STATUS_NEEDS_HUMAN:
                ok = True
            err = None if ok else (
                result.get("error_reason") or _meta.error_reason or "failed"
            )
            return ToolResult(ok=ok, payload=dict(result), error_reason=err)

    # Still running / no result yet.
    payload = make_success_payload(
        mode=meta.mode,
        run_id=meta.run_id or meta.job_id,
        status=meta.status if meta.status else STATUS_RUNNING,
        summary="job running" if meta.status == JOB_STATUS_RUNNING else meta.status,
        job_id=meta.job_id,
        log_path=str(run_dir_for(ctx.paths, meta.job_id) / RESULT_NAME),
        exit_code=meta.exit_code,
    )
    return ToolResult(ok=True, payload=payload)


def _timeout_for(mode: Mode, args: dict[str, Any]) -> float:
    default = default_timeout_s(mode) or 600
    cap = MAX_TIMEOUT_S.get(mode, default)
    # Schema v1 has no timeout_seconds; use mode default capped.
    return float(min(default, cap))


def _finalize_sync(
    *,
    ctx: ToolContext,
    job_id: str,
    mode: Mode,
    proc_result: Any,
    access_token: str | None,
) -> ToolResult:
    """Write logs, harvest, usage_bridge, redact; return ToolResult."""
    run_dir = run_dir_for(ctx.paths, job_id)
    try:
        (run_dir / STDOUT_NAME).write_text(proc_result.stdout or "", encoding="utf-8")
        (run_dir / STDERR_NAME).write_text(proc_result.stderr or "", encoding="utf-8")
    except OSError as exc:
        _LOG.warning("grok_build write logs failed job_id=%s: %s", job_id, exc)

    timed_out = bool(getattr(proc_result, "timed_out", False))
    exit_code = int(getattr(proc_result, "exit_code", -1))
    meter = _meter_from_ctx(ctx)

    known: list[str] = []
    if access_token:
        known = merge_known_values([access_token])
    try:
        from elyra.llm.auth import auth_secret_values_for_redaction

        known = merge_known_values(known, auth_secret_values_for_redaction(ctx.paths.data_dir))
    except Exception:  # noqa: BLE001 — redaction best-effort
        pass

    try:
        _meta, result = finalize_job(
            ctx.paths,
            job_id,
            meter=meter,
            exit_code=exit_code,
            timed_out=timed_out,
            known_values=known or None,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the tool
        _LOG.exception("grok_build finalize failed job_id=%s", job_id)
        shred_tokens(run_dir)
        return _err(
            "skill_failed",
            mode=mode,
            hint=f"finalize failed: {type(exc).__name__}",
            job_id=job_id,
        )

    # Ensure call-local access is redacted even if finalize skipped known_values paths.
    if known:
        result = redact_result_payload(result, known)

    status = str(result.get("status") or STATUS_FAILED)
    if status == STATUS_NEEDS_HUMAN:
        return ToolResult(ok=True, payload=result)
    if result.get("ok") and status == STATUS_COMPLETED:
        return ToolResult(ok=True, payload=result)
    reason = result.get("error_reason") or (
        "timeout" if timed_out else "nonzero_exit"
    )
    return ToolResult(ok=False, payload=result, error_reason=str(reason))


def grok_build(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Thin host builtin: validate → instrument run/spawn → ToolResult.

    Never logs tokens. Never assigns OAuth access into ``ctx.extras["secret_env"]``.
    """
    if not isinstance(args, dict):
        return _err("invalid_args", hint="args must be an object")

    # --- Poll path (job_id wins) ---
    if is_poll_only(args):
        job_id = str(args.get("job_id") or "").strip()
        return _poll_job(job_id, ctx)

    mode = Mode.parse(args.get("mode"))
    if mode is None:
        return _err("invalid_args", hint="mode is required and must be a known enum value")

    # deep_research experimental gate early (also in validate).
    if mode is Mode.DEEP_RESEARCH and DEEP_RESEARCH_EXPERIMENTAL:
        # Allow override via extras for tests / post-spike enable.
        enabled = False
        if isinstance(ctx.extras, dict) and ctx.extras.get("deep_research_enabled"):
            enabled = True
        if not enabled:
            return _err(
                "mode_experimental",
                mode=mode,
                hint="deep_research awaits headless spike sign-off (PR0a)",
            )

    # Meter pre-check
    meter = _meter_from_ctx(ctx)
    usage_allowed = meter_allows_call(meter)

    # Path jail for cwd / repo
    cwd_or_err = _resolve_cwd(args, ctx)
    if isinstance(cwd_or_err, ToolResult):
        # Validate may also want missing_repo — return jail result as-is.
        return cwd_or_err
    cwd = cwd_or_err

    # execute_plan: design doc + base branch preflight
    design_doc_path: str | None = None
    base_branch = DEFAULT_BASE_BRANCH
    raw_base = args.get("base_branch")
    if isinstance(raw_base, str) and raw_base.strip():
        base_branch = raw_base.strip()

    design_doc_exists: bool | None = None
    base_branch_ok: bool | None = None

    if mode is Mode.EXECUTE_PLAN:
        raw_doc = args.get("design_doc_path")
        if not isinstance(raw_doc, str) or not raw_doc.strip():
            return _err(
                "missing_design_doc_path",
                mode=mode,
                hint="execute_plan requires design_doc_path",
            )
        jailed = _jail_file_path(raw_doc.strip(), ctx, base=cwd)
        if isinstance(jailed, ToolResult):
            return jailed
        design_doc_path = str(jailed)
        design_doc_exists = jailed.is_file()
        if not design_doc_exists:
            return _err(
                "design_doc_missing",
                mode=mode,
                hint=f"design doc is not a file: {design_doc_path}",
                design_doc_path=design_doc_path,
            )
        base_branch_ok = _base_branch_exists(cwd, base_branch)
        if not base_branch_ok:
            return _err(
                "base_branch_missing",
                mode=mode,
                hint=(
                    f"base branch {base_branch!r} not found locally or as "
                    f"origin/{base_branch}; create/push working first"
                ),
                base_branch=base_branch,
            )

    # Mode-conditional validate (table)
    err_reason = validate_grok_build_args(
        args,
        design_doc_exists=design_doc_exists,
        base_branch_ok=base_branch_ok,
        jobs_ready=True,  # PR3 landed; durable jobs available
        deep_research_enabled=(
            not DEEP_RESEARCH_EXPERIMENTAL
            or (
                isinstance(ctx.extras, dict)
                and bool(ctx.extras.get("deep_research_enabled"))
            )
        ),
        usage_allowed=usage_allowed,
        repo_resolved=True,
        check_design_doc_fs=False,  # already checked above for execute_plan
    )
    if err_reason is not None:
        return _err(err_reason, mode=mode)

    # Single OAuth mint (KD-F13) — fail-closed before create_job when possible.
    # NEVER assign into secret_env; NEVER put access in meta; NEVER XAI_API_KEY.
    try:
        fresh = ensure_fresh_access(Path(ctx.paths.data_dir))
    except Exception as exc:  # noqa: BLE001 — treat mint failures as unavailable
        _LOG.warning("grok_build ensure_fresh_access failed: %s", type(exc).__name__)
        return _err(
            "auth_unavailable",
            mode=mode,
            hint="xai_oauth login required (elyra auth login / Glass)",
        )
    if not fresh.ok or not fresh.access_token:
        return _err(
            "auth_unavailable",
            mode=mode,
            hint="xai_oauth login required (elyra auth login / Glass)",
        )
    access_token = str(fresh.access_token).strip()
    if not access_token:
        return _err(
            "auth_unavailable",
            mode=mode,
            hint="xai_oauth login required (elyra auth login / Glass)",
        )
    expires_at = fresh.expires_at
    if not expires_at:
        # Rare: store omitted expires_at but access ok — derive default window.
        expires_at = expires_at_from_expires_in(3600)
    # Law: never put OAuth access into ctx.extras["secret_env"].
    # (Do not assign; guest never merges secret_env; host seeds auth.json + provider.)

    # Discover grok binary
    try:
        grok_bin = find_grok_binary()
    except GrokNotFoundError:
        return _err(
            "grok_not_found",
            mode=mode,
            hint="install Grok Build or set GROK_BIN",
        )

    timeout_s = _timeout_for(mode, args)
    use_async = _want_async(mode, args)

    # Create job / run_dir early so seed + logs have a home.
    try:
        meta = create_job(
            ctx.paths,
            mode=mode.value,
            async_job=use_async,
            base_branch=base_branch if mode is Mode.EXECUTE_PLAN else None,
            cwd=str(cwd),
            timeout_s=timeout_s,
            status=JOB_STATUS_RUNNING,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("grok_build create_job failed")
        return _err(
            "skill_failed",
            mode=mode,
            hint=f"create_job failed: {type(exc).__name__}",
        )

    job_id = meta.job_id
    run_dir = run_dir_for(ctx.paths, job_id)
    artifacts_dir = run_dir / ARTIFACTS_DIR_NAME

    # Seed isolated GROK_HOME: skills + config + access-only auth.json (KD-F2).
    # Pass the same mint into seed (async must not discard token before seed).
    try:
        seeded = seed_isolated_home(
            run_dir,
            data_dir=ctx.paths.data_dir,
            grok_bin=grok_bin,
            access_token=access_token,
            expires_at=expires_at,
        )
    except GrokSkillsUnavailableError as exc:
        return _terminalize_orphan(
            ctx,
            job_id,
            mode=mode,
            error_reason="grok_skills_unavailable",
            hint=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("grok_build seed_isolated_home failed")
        return _terminalize_orphan(
            ctx,
            job_id,
            mode=mode,
            error_reason="grok_skills_unavailable",
            hint=f"seed failed: {type(exc).__name__}",
        )

    # Build argv (effort only inside -p body)
    effort = args.get("effort")
    effort_i = int(effort) if isinstance(effort, int) and not isinstance(effort, bool) else None
    max_turns = args.get("max_turns")
    max_turns_i = (
        int(max_turns)
        if isinstance(max_turns, int) and not isinstance(max_turns, bool)
        else None
    )
    target = args.get("target") if isinstance(args.get("target"), str) else None
    prompt = args.get("prompt") if isinstance(args.get("prompt"), str) else None

    argv, _body = build_argv_for_mode(
        mode,
        prompt=prompt,
        design_doc_path=design_doc_path,
        effort=effort_i,
        target=target,
        artifacts_dir=artifacts_dir,
        cwd=cwd,
        always_approve=True,
        max_turns=max_turns_i,
        grok_bin=str(grok_bin),
    )
    try:
        update_job(ctx.paths, job_id, argv=list(argv))
    except Exception:  # noqa: BLE001
        pass

    # Child env: GROK_AUTH_PROVIDER_COMMAND + optional ELYRA_DATA_DIR (KD-F5).
    # Never XAI_API_KEY from OAuth (KD-F4).
    provider_cmd = seeded.auth_provider_command
    data_dir = seeded.data_dir

    if use_async:
        # Non-blocking spawn; reaper owns wait/finalize.
        try:
            spawned = spawn_grok(
                argv,
                grok_home=seeded.grok_home,
                cwd=cwd,
                stdout_path=run_dir / STDOUT_NAME,
                stderr_path=run_dir / STDERR_NAME,
                auth_provider_command=provider_cmd,
                data_dir=data_dir,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("grok_build spawn failed job_id=%s", job_id)
            return _terminalize_orphan(
                ctx,
                job_id,
                mode=mode,
                error_reason="skill_failed",
                hint=f"spawn failed: {type(exc).__name__}",
            )
        try:
            update_job(
                ctx.paths,
                job_id,
                pid=spawned.pid,
                pgid=spawned.pgid,
            )
        except Exception:  # noqa: BLE001
            pass

        payload = make_success_payload(
            mode=mode,
            run_id=job_id,
            status=STATUS_RUNNING,
            summary="job spawned; poll job_id or wait for background wake",
            job_id=job_id,
            log_path=str(run_dir / RESULT_NAME),
            exit_code=None,
            extra={"async": True, "pid": spawned.pid},
        )
        # Do not put access token in result/meta; shred on finalize only.
        return ToolResult(ok=True, payload=payload)

    # Sync path (prompt default or async=false)
    try:
        proc_result = run_grok(
            argv,
            grok_home=seeded.grok_home,
            cwd=cwd,
            timeout_s=timeout_s,
            auth_provider_command=provider_cmd,
            data_dir=data_dir,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("grok_build run_grok failed job_id=%s", job_id)
        return _terminalize_orphan(
            ctx,
            job_id,
            mode=mode,
            error_reason="skill_failed",
            hint=f"run failed: {type(exc).__name__}",
        )

    try:
        update_job(
            ctx.paths,
            job_id,
            pid=proc_result.pid,
            pgid=proc_result.pgid,
            exit_code=proc_result.exit_code,
            timed_out=proc_result.timed_out,
        )
    except Exception:  # noqa: BLE001
        pass

    return _finalize_sync(
        ctx=ctx,
        job_id=job_id,
        mode=mode,
        proc_result=proc_result,
        access_token=access_token,
    )


__all__ = ["grok_build"]
