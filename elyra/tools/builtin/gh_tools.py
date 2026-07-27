"""Host builtin gh_* tools (frozen PR7 set) with GH_TOKEN soft-fail.

Frozen names only: gh_auth_status, gh_pr_{create,list,view},
gh_issue_{create,list}, gh_api, gh_project_{list,item_list,item_add,
item_edit,field_list}.

Auth: read ``ctx.extras["secret_env"]`` for ``GH_TOKEN``; soft-fail
``auth_unavailable`` when missing (registry never invents that reason).
Mockable ``run_gh`` for hermetic tests (no network).
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Sequence

from elyra.tools.types import ToolContext, ToolResult

_DEFAULT_TIMEOUT = 60.0
_GH_TOKEN_ENV = "GH_TOKEN"

_AUTH_HINT = (
    "Set secret gh_token (Glass Secrets panel) and grant this tool; "
    "GH_TOKEN is injected call-locally only."
)


def run_gh(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` argv (no shell). Injectable for tests."""
    full_env = os.environ.copy()
    # Do not leak ambient GH_TOKEN into tool runs unless call-local env sets it.
    full_env.pop("GH_TOKEN", None)
    full_env.pop("GITHUB_TOKEN", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=float(timeout),
        env=full_env,
        check=False,
        shell=False,
    )


def _secret_env(ctx: ToolContext) -> dict[str, str]:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    raw = extras.get("secret_env")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


def _require_gh_token(ctx: ToolContext) -> dict[str, str] | ToolResult:
    """Return env fragment with GH_TOKEN or auth_unavailable ToolResult."""
    secret_env = _secret_env(ctx)
    token = secret_env.get(_GH_TOKEN_ENV) or secret_env.get("GITHUB_TOKEN")
    if not token:
        return ToolResult(
            ok=False,
            payload={
                "hint": _AUTH_HINT,
                "secret": "gh_token",
                "env_var": _GH_TOKEN_ENV,
            },
            error_reason="auth_unavailable",
        )
    return {_GH_TOKEN_ENV: token}


def _timeout_arg(args: dict[str, Any]) -> float | ToolResult:
    raw = args.get("timeout", _DEFAULT_TIMEOUT)
    if raw is None:
        return _DEFAULT_TIMEOUT
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")
    if float(raw) <= 0:
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")
    return float(raw)


def _str_arg(args: dict[str, Any], key: str) -> str | None:
    raw = args.get(key)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _invoke(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    cwd: str | None = None,
) -> ToolResult:
    try:
        proc = run_gh(argv, cwd=cwd, timeout=timeout, env=env)
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            payload={
                "argv": argv,
                "hint": "gh CLI not found on PATH; install GitHub CLI",
            },
            error_reason="gh_unavailable",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            payload={"argv": argv},
            error_reason="timeout",
        )
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={"argv": argv, "error": type(exc).__name__},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    # Never put token into payload.
    return ToolResult(
        ok=proc.returncode == 0,
        payload={
            "argv": list(argv),
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        },
        error_reason=None if proc.returncode == 0 else "gh_failed",
    )


def _repo_flags(args: dict[str, Any]) -> list[str]:
    """Optional --repo owner/name."""
    repo = _str_arg(args, "repo")
    if repo:
        return ["--repo", repo]
    return []


# ---------------------------------------------------------------------------
# Frozen gh_* handlers
# ---------------------------------------------------------------------------


def gh_auth_status(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh auth status`` — soft-fail without token."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    return _invoke(["gh", "auth", "status"], env=env, timeout=timeout)


def gh_pr_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh pr list``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["gh", "pr", "list", *_repo_flags(args)]
    state = _str_arg(args, "state")
    if state:
        argv.extend(["--state", state])
    limit = args.get("limit")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and int(limit) > 0:
        argv.extend(["--limit", str(int(limit))])
    if args.get("json") is True or isinstance(args.get("json_fields"), str):
        fields = _str_arg(args, "json_fields") or "number,title,state,url"
        argv.extend(["--json", fields])
    return _invoke(argv, env=env, timeout=timeout)


def gh_pr_create(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh pr create``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    title = _str_arg(args, "title")
    if not title:
        return ToolResult(ok=False, payload={}, error_reason="missing_title")
    argv = ["gh", "pr", "create", *_repo_flags(args), "--title", title]
    body = _str_arg(args, "body")
    if body:
        argv.extend(["--body", body])
    else:
        argv.append("--fill")
    base = _str_arg(args, "base")
    if base:
        argv.extend(["--base", base])
    head = _str_arg(args, "head")
    if head:
        argv.extend(["--head", head])
    draft = args.get("draft")
    if draft is True:
        argv.append("--draft")
    return _invoke(argv, env=env, timeout=timeout)


def gh_pr_view(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh pr view`` by number or URL."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    number = args.get("number") or args.get("pr")
    if number is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_number")
    argv = ["gh", "pr", "view", str(number), *_repo_flags(args)]
    if args.get("json") is True or isinstance(args.get("json_fields"), str):
        fields = _str_arg(args, "json_fields") or "number,title,state,url,body"
        argv.extend(["--json", fields])
    return _invoke(argv, env=env, timeout=timeout)


def gh_issue_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh issue list``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["gh", "issue", "list", *_repo_flags(args)]
    state = _str_arg(args, "state")
    if state:
        argv.extend(["--state", state])
    limit = args.get("limit")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and int(limit) > 0:
        argv.extend(["--limit", str(int(limit))])
    if args.get("json") is True or isinstance(args.get("json_fields"), str):
        fields = _str_arg(args, "json_fields") or "number,title,state,url"
        argv.extend(["--json", fields])
    return _invoke(argv, env=env, timeout=timeout)


def gh_issue_create(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh issue create``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    title = _str_arg(args, "title")
    if not title:
        return ToolResult(ok=False, payload={}, error_reason="missing_title")
    argv = ["gh", "issue", "create", *_repo_flags(args), "--title", title]
    body = _str_arg(args, "body")
    if body:
        argv.extend(["--body", body])
    return _invoke(argv, env=env, timeout=timeout)


def gh_api(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh api <endpoint>`` escape hatch."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    endpoint = _str_arg(args, "endpoint") or _str_arg(args, "path")
    if not endpoint:
        return ToolResult(ok=False, payload={}, error_reason="missing_endpoint")
    argv = ["gh", "api", endpoint]
    method = _str_arg(args, "method")
    if method:
        argv.extend(["--method", method.upper()])
    # field key=value pairs
    fields = args.get("fields")
    if isinstance(fields, dict):
        for k, v in fields.items():
            if isinstance(k, str):
                argv.extend(["-f", f"{k}={v}"])
    raw_fields = args.get("raw_fields")
    if isinstance(raw_fields, dict):
        for k, v in raw_fields.items():
            if isinstance(k, str):
                argv.extend(["-F", f"{k}={v}"])
    input_body = _str_arg(args, "input")
    if input_body:
        argv.extend(["--input", "-"])
        # Prefer -f/-F; for raw JSON body use --input via stdin — keep simple:
        # if input provided as string path-like, pass through; else skip stdin.
    jq = _str_arg(args, "jq")
    if jq:
        argv.extend(["--jq", jq])
    return _invoke(argv, env=env, timeout=timeout)


def gh_project_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh project list`` for owner."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    argv = ["gh", "project", "list"]
    owner = _str_arg(args, "owner")
    if owner:
        argv.extend(["--owner", owner])
    limit = args.get("limit")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and int(limit) > 0:
        argv.extend(["--limit", str(int(limit))])
    if args.get("format_json") is True or args.get("json") is True:
        argv.extend(["--format", "json"])
    return _invoke(argv, env=env, timeout=timeout)


def gh_project_item_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh project item-list``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    number = args.get("number") or args.get("project_number")
    if number is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_number")
    argv = ["gh", "project", "item-list", str(number)]
    owner = _str_arg(args, "owner")
    if owner:
        argv.extend(["--owner", owner])
    limit = args.get("limit")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and int(limit) > 0:
        argv.extend(["--limit", str(int(limit))])
    if args.get("format_json") is True or args.get("json") is True:
        argv.extend(["--format", "json"])
    return _invoke(argv, env=env, timeout=timeout)


def gh_project_item_add(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh project item-add``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    number = args.get("number") or args.get("project_number")
    if number is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_number")
    url = _str_arg(args, "url")
    if not url:
        return ToolResult(ok=False, payload={}, error_reason="missing_url")
    argv = ["gh", "project", "item-add", str(number), "--url", url]
    owner = _str_arg(args, "owner")
    if owner:
        argv.extend(["--owner", owner])
    if args.get("format_json") is True or args.get("json") is True:
        argv.extend(["--format", "json"])
    return _invoke(argv, env=env, timeout=timeout)


def gh_project_item_edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh project item-edit`` — field updates on a project item."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    item_id = _str_arg(args, "id") or _str_arg(args, "item_id")
    if not item_id:
        return ToolResult(ok=False, payload={}, error_reason="missing_id")
    argv = ["gh", "project", "item-edit", "--id", item_id]
    project_id = _str_arg(args, "project_id")
    if project_id:
        argv.extend(["--project-id", project_id])
    field_id = _str_arg(args, "field_id")
    if field_id:
        argv.extend(["--field-id", field_id])
    text = _str_arg(args, "text")
    if text is not None:
        argv.extend(["--text", text])
    single_select = _str_arg(args, "single_select_option_id")
    if single_select:
        argv.extend(["--single-select-option-id", single_select])
    number_val = args.get("number")
    if isinstance(number_val, (int, float)) and not isinstance(number_val, bool):
        argv.extend(["--number", str(number_val)])
    if args.get("format_json") is True or args.get("json") is True:
        argv.extend(["--format", "json"])
    return _invoke(argv, env=env, timeout=timeout)


def gh_project_field_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``gh project field-list``."""
    env = _require_gh_token(ctx)
    if isinstance(env, ToolResult):
        return env
    timeout = _timeout_arg(args)
    if isinstance(timeout, ToolResult):
        return timeout
    number = args.get("number") or args.get("project_number")
    if number is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_number")
    argv = ["gh", "project", "field-list", str(number)]
    owner = _str_arg(args, "owner")
    if owner:
        argv.extend(["--owner", owner])
    if args.get("format_json") is True or args.get("json") is True:
        argv.extend(["--format", "json"])
    return _invoke(argv, env=env, timeout=timeout)


# Frozen set exported for tests / catalog assertions.
FROZEN_GH_TOOLS: tuple[str, ...] = (
    "gh_auth_status",
    "gh_pr_create",
    "gh_pr_list",
    "gh_pr_view",
    "gh_issue_create",
    "gh_issue_list",
    "gh_api",
    "gh_project_list",
    "gh_project_item_list",
    "gh_project_item_add",
    "gh_project_item_edit",
    "gh_project_field_list",
)

FROZEN_GIT_TOOLS: tuple[str, ...] = (
    "git_status",
    "git_diff",
    "git_log",
    "git_add",
    "git_commit",
    "git_branch",
    "git_checkout",
    "git_worktree_add",
    "git_worktree_list",
    "git_worktree_remove",
    "git_worktree_prune",
)


__all__ = [
    "FROZEN_GH_TOOLS",
    "FROZEN_GIT_TOOLS",
    "gh_api",
    "gh_auth_status",
    "gh_issue_create",
    "gh_issue_list",
    "gh_pr_create",
    "gh_pr_list",
    "gh_pr_view",
    "gh_project_field_list",
    "gh_project_item_add",
    "gh_project_item_edit",
    "gh_project_item_list",
    "gh_project_list",
    "run_gh",
]
