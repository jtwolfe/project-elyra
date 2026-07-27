"""Host builtin git_* tools (frozen PR7 set) with VCS path jail.

Frozen names only: git_status, git_diff, git_log, git_add, git_commit,
git_branch, git_checkout, git_worktree_{add,list,remove,prune}.
Argv wrappers (shell=False); mockable ``run_git`` for hermetic tests.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from elyra.settings import Settings, default_settings
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.vcs_jail import (
    PathJailError,
    effective_allowed_roots,
    resolve_repo_path,
)

# Default subprocess timeout (seconds).
_DEFAULT_TIMEOUT = 60.0

# Module-level runner — tests monkeypatch ``run_git``.
def run_git(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` argv (no shell). Injectable for tests."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Avoid interactive prompts in agent context.
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=float(timeout),
        env=full_env,
        check=False,
        shell=False,
    )


def _settings(ctx: ToolContext) -> Settings:
    if ctx.settings is not None:
        return ctx.settings
    return default_settings()


def _resolve_repo(
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    key: str = "repo",
    require_git: bool = True,
) -> Path | ToolResult:
    raw = args.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"missing_{key}",
        )
    roots = effective_allowed_roots(_settings(ctx), ctx.paths)
    try:
        return resolve_repo_path(raw, roots, require_git=require_git)
    except PathJailError as exc:
        return ToolResult(
            ok=False,
            payload={"path": raw.strip(), "repo": raw.strip()},
            error_reason=exc.reason,
        )


def _resolve_path_in_jail(
    raw: str,
    ctx: ToolContext,
    *,
    require_git: bool = False,
) -> Path | ToolResult:
    if not isinstance(raw, str) or not raw.strip():
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    roots = effective_allowed_roots(_settings(ctx), ctx.paths)
    try:
        return resolve_repo_path(raw, roots, require_git=require_git)
    except PathJailError as exc:
        return ToolResult(
            ok=False,
            payload={"path": raw.strip()},
            error_reason=exc.reason,
        )


def _timeout_arg(args: dict[str, Any]) -> float | ToolResult:
    raw = args.get("timeout", _DEFAULT_TIMEOUT)
    if raw is None:
        return _DEFAULT_TIMEOUT
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")
    if float(raw) <= 0:
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")
    return float(raw)


def _invoke(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> ToolResult:
    try:
        proc = run_git(argv, cwd=cwd, timeout=timeout)
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            payload={"argv": argv, "hint": "git binary not found on PATH"},
            error_reason="git_unavailable",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            payload={"argv": argv, "cwd": str(cwd)},
            error_reason="timeout",
        )
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={"argv": argv, "error": type(exc).__name__},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    return ToolResult(
        ok=proc.returncode == 0,
        payload={
            "argv": list(argv),
            "cwd": str(cwd),
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        },
        error_reason=None if proc.returncode == 0 else "git_failed",
    )


def _str_list(args: dict[str, Any], key: str) -> list[str] | ToolResult | None:
    """Optional list of strings; None if key absent; ToolResult on bad type."""
    if key not in args or args[key] is None:
        return None
    raw = args[key]
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                return ToolResult(
                    ok=False, payload={}, error_reason=f"invalid_{key}"
                )
            if item.strip():
                out.append(item)
        return out
    return ToolResult(ok=False, payload={}, error_reason=f"invalid_{key}")


# ---------------------------------------------------------------------------
# Frozen git_* handlers
# ---------------------------------------------------------------------------


def git_status(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git status`` (optional short/porcelain flags)."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["git", "status"]
    if args.get("short") is True:
        argv.append("-sb")
    elif args.get("porcelain") is True:
        argv.append("--porcelain")
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_diff(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git diff`` — optional staged, paths, commit range."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["git", "diff"]
    if args.get("staged") is True or args.get("cached") is True:
        argv.append("--cached")
    ref = args.get("ref")
    if isinstance(ref, str) and ref.strip():
        argv.append(ref.strip())
    paths = _str_list(args, "paths")
    if isinstance(paths, ToolResult):
        return paths
    if paths:
        argv.append("--")
        argv.extend(paths)
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_log(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git log`` with optional max_count and oneline."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["git", "log"]
    max_count = args.get("max_count", args.get("n", 20))
    if max_count is not None:
        if isinstance(max_count, bool) or not isinstance(max_count, (int, float)):
            return ToolResult(ok=False, payload={}, error_reason="invalid_max_count")
        n = int(max_count)
        if n < 1:
            return ToolResult(ok=False, payload={}, error_reason="invalid_max_count")
        argv.extend(["-n", str(n)])
    if args.get("oneline") is True:
        argv.append("--oneline")
    fmt = args.get("format")
    if isinstance(fmt, str) and fmt.strip():
        argv.append(f"--format={fmt.strip()}")
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_add(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git add`` paths (required non-empty)."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    paths = _str_list(args, "paths")
    if isinstance(paths, ToolResult):
        return paths
    if not paths:
        # Accept single path alias
        one = args.get("path")
        if isinstance(one, str) and one.strip():
            paths = [one.strip()]
        else:
            return ToolResult(ok=False, payload={}, error_reason="missing_paths")
    if args.get("all") is True:
        argv = ["git", "add", "-A"]
    else:
        argv = ["git", "add", "--", *paths]
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_commit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git commit -m <message>``."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_message")
    argv = ["git", "commit", "-m", message]
    if args.get("allow_empty") is True:
        argv.append("--allow-empty")
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_branch(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """List branches or create one when ``name`` is set."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    name = args.get("name")
    if isinstance(name, str) and name.strip():
        argv = ["git", "branch"]
        if args.get("delete") is True:
            argv.append("-d" if args.get("force") is not True else "-D")
        argv.append(name.strip())
        return _invoke(argv, cwd=repo, timeout=timeout)
    # list
    argv = ["git", "branch", "-a"]
    if args.get("verbose") is True:
        argv.append("-v")
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_checkout(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git checkout`` branch/ref; optional ``create`` for -b."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    ref = args.get("ref") or args.get("branch") or args.get("name")
    if not isinstance(ref, str) or not ref.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_ref")
    argv = ["git", "checkout"]
    if args.get("create") is True:
        argv.append("-b")
    argv.append(ref.strip())
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_worktree_add(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git worktree add <path> [<branch>]`` — path must be inside jail."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    path_raw = args.get("path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_path")
    # New worktree path need not exist / have .git yet; must still be in jail.
    roots = effective_allowed_roots(_settings(ctx), ctx.paths)
    try:
        wt_path = resolve_repo_path(
            path_raw.strip(),
            roots,
            require_git=False,
            base=repo.parent,
        )
    except PathJailError as exc:
        return ToolResult(
            ok=False,
            payload={"path": path_raw.strip(), "repo": str(repo)},
            error_reason=exc.reason,
        )

    argv = ["git", "worktree", "add"]
    if args.get("detach") is True:
        argv.append("--detach")
    branch = args.get("branch") or args.get("ref")
    new_branch = args.get("new_branch")
    if isinstance(new_branch, str) and new_branch.strip():
        argv.extend(["-b", new_branch.strip()])
    argv.append(str(wt_path))
    if isinstance(branch, str) and branch.strip():
        argv.append(branch.strip())
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_worktree_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git worktree list``."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["git", "worktree", "list"]
    if args.get("porcelain") is True:
        argv.append("--porcelain")
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_worktree_remove(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git worktree remove`` — dirty trees require ``confirm: true``."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    path_raw = args.get("path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_path")

    # Worktree path must be inside jail (may still have .git).
    wt = _resolve_path_in_jail(path_raw.strip(), ctx, require_git=False)
    if isinstance(wt, ToolResult):
        return wt

    confirm = args.get("confirm") is True
    # Detect dirty working tree at the worktree path when it exists.
    dirty = False
    if wt.is_dir() and (wt / ".git").exists():
        try:
            st = run_git(
                ["git", "status", "--porcelain"],
                cwd=wt,
                timeout=timeout,
            )
            dirty = bool((st.stdout or "").strip())
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            dirty = False

    if dirty and not confirm:
        return ToolResult(
            ok=False,
            payload={
                "path": str(wt),
                "repo": str(repo),
                "dirty": True,
                "hint": "Worktree has local changes; pass confirm=true to force remove",
            },
            error_reason="confirm_required",
        )

    argv = ["git", "worktree", "remove"]
    if dirty and confirm:
        argv.append("--force")
    elif args.get("force") is True:
        if not confirm:
            return ToolResult(
                ok=False,
                payload={
                    "path": str(wt),
                    "hint": "force remove requires confirm=true",
                },
                error_reason="confirm_required",
            )
        argv.append("--force")
    argv.append(str(wt))
    return _invoke(argv, cwd=repo, timeout=timeout)


def git_worktree_prune(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``git worktree prune``."""
    repo = _resolve_repo(args, ctx)
    if isinstance(repo, ToolResult):
        return repo
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["git", "worktree", "prune"]
    if args.get("verbose") is True:
        argv.append("-v")
    return _invoke(argv, cwd=repo, timeout=timeout)


__all__ = [
    "git_add",
    "git_branch",
    "git_checkout",
    "git_commit",
    "git_diff",
    "git_log",
    "git_status",
    "git_worktree_add",
    "git_worktree_list",
    "git_worktree_prune",
    "git_worktree_remove",
    "run_git",
]
