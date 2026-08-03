"""Tests for host builtin grok_build (PR4 + PR-A auth mint/seed).

Hermetic: mock ensure_fresh_access, grok binary, seed, process. Covers
secret_env law, single-mint wiring, validation, missing binary/oauth,
execute_plan preflight.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.instrument.auth_handoff import SeededHome
from elyra.instrument.discover import GrokNotFoundError
from elyra.instrument.jobs import load_job, run_dir_for
from elyra.instrument.modes import DEEP_RESEARCH_EXPERIMENTAL
from elyra.instrument.process import ProcessResult, SpawnedProcess
from elyra.llm.xai_oauth import FreshAccessResult
from elyra.settings import Settings, ToolsSettings
from elyra.tools.builtin import grok_build as gb_mod
from elyra.tools.builtin.grok_build import grok_build
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def repo(home: Path) -> Path:
    r = home / "repo"
    r.mkdir()
    (r / ".git").mkdir()
    # Fake refs for base branch preflight (git may not be fully init'd).
    return r


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(tools=ToolsSettings(allowed_repo_roots=(str(home),)))


@pytest.fixture
def ctx(home: Path, settings: Settings) -> ToolContext:
    return ToolContext(
        paths=resolve_paths(home),
        settings=settings,
        moment_id="moment-gb-1",
        user_id="operator",
        extras={},
    )


@pytest.fixture
def registry(home: Path) -> ToolRegistry:
    return ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )


def _seeded(run_dir: Path, data_dir: Path) -> SeededHome:
    gh = run_dir / "grok_home"
    gh.mkdir(parents=True, exist_ok=True)
    bundled = gh / "bundled"
    bundled.mkdir(exist_ok=True)
    (bundled / "skills" / "design").mkdir(parents=True, exist_ok=True)
    (bundled / "skills" / "implement").mkdir(parents=True, exist_ok=True)
    cfg = gh / "config.toml"
    provider = "/x/python -m elyra.instrument.auth_provider --data-dir /data"
    cfg.write_text(f'[auth]\nauth_provider_command = "{provider}"\n')
    return SeededHome(
        grok_home=gh,
        config_path=cfg,
        bundled_link=bundled,
        real_bundled=bundled,
        auth_provider_command=provider,
        data_dir=data_dir,
        auth_json_path=gh / "auth.json",
    )


def _fresh(
    access: str | None = "test-access-token-xyz",
    *,
    ok: bool | None = None,
    expires_at: str | None = "2026-08-03T06:42:10Z",
) -> FreshAccessResult:
    if ok is None:
        ok = access is not None and bool(str(access).strip())
    return FreshAccessResult(
        ok=bool(ok),
        access_token=access,
        expires_at=expires_at if ok else None,
        email="instrument@elyra.local" if ok else None,
        detail=None if ok else "missing",
        rotated=False,
    )


def _stub_ready(
    monkeypatch: pytest.MonkeyPatch,
    *,
    access: str | None = "test-access-token-xyz",
    expires_at: str | None = "2026-08-03T06:42:10Z",
    grok_bin: Path | None = None,
    seed: bool = True,
) -> dict[str, Any]:
    """Wire mocks so spawn/run paths can proceed past preflight."""
    seen: dict[str, Any] = {
        "access": access,
        "expires_at": expires_at,
        "mint_calls": 0,
        "seed_kwargs": None,
    }

    def _ensure(data_dir: Any, **_k: Any) -> FreshAccessResult:
        seen["mint_calls"] += 1
        seen["mint_data_dir"] = data_dir
        return _fresh(access, expires_at=expires_at)

    monkeypatch.setattr(gb_mod, "ensure_fresh_access", _ensure)

    if grok_bin is None:
        grok_bin = Path("/usr/bin/fake-grok")

    def _find(**_k: Any) -> Path:
        if grok_bin is None:
            raise GrokNotFoundError("no")
        return Path(grok_bin)

    monkeypatch.setattr(gb_mod, "find_grok_binary", _find)

    if seed:

        def _seed(run_dir: Path | str, **kw: Any) -> SeededHome:
            data_dir = Path(kw["data_dir"])
            seen["seed_run_dir"] = Path(run_dir)
            seen["seed_kwargs"] = dict(kw)
            return _seeded(Path(run_dir), data_dir)

        monkeypatch.setattr(gb_mod, "seed_isolated_home", _seed)

    # Base branch always ok unless overridden.
    monkeypatch.setattr(gb_mod, "_base_branch_exists", lambda repo, branch: True)

    return seen


def test_package_discovered(registry: ToolRegistry) -> None:
    assert registry.has("grok_build")
    pkg = registry.get("grok_build")
    assert pkg is not None
    assert pkg.meta.name == "grok_build"
    assert pkg.handler is not None
    assert pkg.meta.kind == "integrate"
    schema = pkg.meta.parameters
    assert schema.get("required") == ["mode"]
    assert schema.get("additionalProperties") is False
    modes = schema["properties"]["mode"]["enum"]
    assert set(modes) == {
        "prompt",
        "design",
        "implement",
        "execute_plan",
        "deep_research",
        "review",
    }


def test_never_assigns_oauth_to_secret_env(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named PR4 law: access token must not land in ctx.extras['secret_env']."""
    token = "super-secret-oauth-access-token-abc"
    _stub_ready(monkeypatch, access=token)

    def fake_run(argv, **kw: Any) -> ProcessResult:
        return ProcessResult(
            exit_code=0,
            stdout=json.dumps({"text": "done", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}),
            stderr="",
            timed_out=False,
            pid=111,
            pgid=111,
        )

    monkeypatch.setattr(gb_mod, "run_grok", fake_run)

    # Poison extras with empty secret_env as registry would; handler must not fill it.
    ctx.extras["secret_env"] = {}

    result = grok_build(
        {"mode": "prompt", "prompt": "hello", "cwd": str(repo), "async": False},
        ctx,
    )
    assert result.ok, result
    secret_env = ctx.extras.get("secret_env")
    assert secret_env is not None
    assert token not in secret_env.values()
    assert "XAI_ACCESS_TOKEN" not in secret_env
    assert "XAI_API_KEY" not in secret_env
    # Token must not appear in model-visible payload.
    blob = json.dumps(result.payload)
    assert token not in blob


def test_missing_oauth_fail_closed(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch, access=None)
    result = grok_build(
        {"mode": "prompt", "prompt": "hi", "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "auth_unavailable"


def test_ensure_fresh_access_exception_fail_closed(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mint exception maps to auth_unavailable before create_job (no job left)."""
    seen = _stub_ready(monkeypatch)

    def _boom(_data_dir: Any, **_k: Any) -> FreshAccessResult:
        seen["mint_calls"] += 1
        raise RuntimeError("oauth store unreadable")

    monkeypatch.setattr(gb_mod, "ensure_fresh_access", _boom)
    result = grok_build(
        {"mode": "prompt", "prompt": "hi", "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "auth_unavailable"
    assert seen["mint_calls"] == 1
    assert seen["seed_kwargs"] is None
    # Mint is before create_job — no durable job/meta left behind.
    runtime = Path(ctx.paths.data_dir) / "runtime" / "grok_build"
    if runtime.is_dir():
        assert list(runtime.iterdir()) == []


def test_expires_at_fallback_when_store_omits_expiry(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§1.2: ok access without expires_at → expires_at_from_expires_in(3600)."""
    import re

    token = "tok-no-expiry-in-store"
    seen = _stub_ready(monkeypatch, access=token, expires_at=None)

    def fake_run(argv, **kw: Any) -> ProcessResult:
        return ProcessResult(
            exit_code=0,
            stdout='{"text":"ok","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}',
            stderr="",
            timed_out=False,
            pid=12,
            pgid=12,
        )

    monkeypatch.setattr(gb_mod, "run_grok", fake_run)
    result = grok_build(
        {"mode": "prompt", "prompt": "hi", "cwd": str(repo), "async": False},
        ctx,
    )
    assert result.ok, result
    assert seen["mint_calls"] == 1
    sk = seen["seed_kwargs"]
    assert sk is not None
    assert sk.get("access_token") == token
    exp = sk.get("expires_at")
    assert isinstance(exp, str) and exp
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", exp)


def test_single_mint_passes_token_and_expiry_to_seed(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KD-F13: one ensure_fresh_access; seed receives access_token + expires_at."""
    token = "mint-access-token-single"
    exp = "2026-09-01T12:00:00Z"
    seen = _stub_ready(monkeypatch, access=token, expires_at=exp)

    def fake_run(argv, **kw: Any) -> ProcessResult:
        # Child env must carry provider command; never XAI_API_KEY from OAuth.
        assert kw.get("auth_provider_command")
        assert "XAI_API_KEY" not in (kw.get("extra_env") or {})
        return ProcessResult(
            exit_code=0,
            stdout='{"text":"ok","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}',
            stderr="",
            timed_out=False,
            pid=11,
            pgid=11,
        )

    monkeypatch.setattr(gb_mod, "run_grok", fake_run)
    result = grok_build(
        {"mode": "prompt", "prompt": "hi", "cwd": str(repo), "async": False},
        ctx,
    )
    assert result.ok, result
    assert seen["mint_calls"] == 1
    sk = seen["seed_kwargs"]
    assert sk is not None
    assert sk.get("access_token") == token
    assert sk.get("expires_at") == exp
    # Token must not appear in payload / meta.
    blob = json.dumps(result.payload)
    assert token not in blob
    job_id = result.payload.get("job_id")
    assert job_id
    meta = load_job(ctx.paths, job_id)
    assert meta is not None
    meta_raw = (run_dir_for(ctx.paths, job_id) / "meta.json").read_text(encoding="utf-8")
    assert token not in meta_raw


def test_async_seed_receives_mint_before_discard(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async path must pass the same mint into seed (not drop after preflight)."""
    token = "async-mint-token-zzz"
    exp = "2026-10-01T00:00:00Z"
    seen = _stub_ready(monkeypatch, access=token, expires_at=exp)
    spawn_kw: dict[str, Any] = {}

    def fake_spawn(argv, **kw: Any) -> SpawnedProcess:
        spawn_kw.update(kw)
        out = Path(kw["stdout_path"])
        err = Path(kw["stderr_path"])
        out.write_text("", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return SpawnedProcess(pid=777, pgid=777, stdout_path=out, stderr_path=err)

    monkeypatch.setattr(gb_mod, "spawn_grok", fake_spawn)
    result = grok_build(
        {"mode": "design", "prompt": "design it", "cwd": str(repo)},
        ctx,
    )
    assert result.ok
    assert seen["mint_calls"] == 1
    assert seen["seed_kwargs"]["access_token"] == token
    assert seen["seed_kwargs"]["expires_at"] == exp
    assert spawn_kw.get("auth_provider_command")
    assert spawn_kw.get("data_dir") is not None
    # No XAI_API_KEY inject path.
    assert "XAI_API_KEY" not in str(spawn_kw.get("extra_env") or {})
    assert token not in json.dumps(result.payload)


def test_no_xai_api_key_from_oauth_in_handler_source() -> None:
    """Static guard: primary path must not set XAI_API_KEY from OAuth access."""
    src = Path(gb_mod.__file__).read_text(encoding="utf-8")
    assert "ensure_fresh_access" in src
    # Never assign XAI_API_KEY from OAuth on the primary path.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "XAI_API_KEY" in stripped and "=" in stripped:
            assert "never" in stripped.lower() or stripped.startswith("assert")


def test_missing_grok_binary(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gb_mod,
        "ensure_fresh_access",
        lambda *_a, **_k: _fresh("tok"),
    )

    def _missing(**_k: Any) -> Path:
        raise GrokNotFoundError("gone")

    monkeypatch.setattr(gb_mod, "find_grok_binary", _missing)
    result = grok_build(
        {"mode": "prompt", "prompt": "hi", "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "grok_not_found"


def test_validate_missing_prompt(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    result = grok_build({"mode": "prompt", "cwd": str(repo)}, ctx)
    assert not result.ok
    assert result.error_reason == "missing_prompt"


def test_validate_design_missing_prompt(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    result = grok_build({"mode": "design", "cwd": str(repo)}, ctx)
    assert not result.ok
    assert result.error_reason == "missing_prompt"


def test_deep_research_experimental(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DEEP_RESEARCH_EXPERIMENTAL is True
    _stub_ready(monkeypatch)
    result = grok_build(
        {"mode": "deep_research", "prompt": "query", "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "mode_experimental"


def test_execute_plan_missing_design_doc_path(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    result = grok_build(
        {"mode": "execute_plan", "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "missing_design_doc_path"


def test_execute_plan_design_doc_missing_file(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    missing = repo / "no-such-design.md"
    result = grok_build(
        {
            "mode": "execute_plan",
            "cwd": str(repo),
            "design_doc_path": str(missing),
        },
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "design_doc_missing"


def test_execute_plan_base_branch_missing(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    monkeypatch.setattr(gb_mod, "_base_branch_exists", lambda *_a, **_k: False)
    doc = repo / "design.md"
    doc.write_text("# design\n", encoding="utf-8")
    result = grok_build(
        {
            "mode": "execute_plan",
            "cwd": str(repo),
            "design_doc_path": str(doc),
        },
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "base_branch_missing"


def test_path_jail_refuses_outside_roots(
    ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    result = grok_build(
        {"mode": "prompt", "prompt": "x", "cwd": "/etc"},
        ctx,
    )
    assert not result.ok
    assert result.error_reason in {"path_jail", "not_a_repo", "invalid_path", "missing_repo"}


def test_missing_repo_when_no_cwd(
    ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With jail roots limited to tmp home (no project_root), missing cwd fails."""
    _stub_ready(monkeypatch)
    result = grok_build({"mode": "prompt", "prompt": "x"}, ctx)
    assert not result.ok
    assert result.error_reason == "missing_repo"


def test_sync_prompt_run_harvest(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)

    def fake_run(argv, **kw: Any) -> ProcessResult:
        assert argv[0].endswith("fake-grok") or "fake-grok" in argv[0]
        assert "-p" in argv
        assert "--output-format" in argv
        assert kw.get("timeout_s", 0) > 0
        return ProcessResult(
            exit_code=0,
            stdout='{"text":"hello world","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}',
            stderr="",
            timed_out=False,
            pid=42,
            pgid=42,
        )

    monkeypatch.setattr(gb_mod, "run_grok", fake_run)
    result = grok_build(
        {"mode": "prompt", "prompt": "say hi", "cwd": str(repo), "async": False},
        ctx,
    )
    assert result.ok
    assert result.payload.get("status") in {"completed", "needs_human"}
    assert result.payload.get("job_id")
    assert "hello world" in (result.payload.get("summary") or "")


def test_async_design_returns_job_id(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    seen: dict[str, Any] = {}

    def fake_spawn(argv, **kw: Any) -> SpawnedProcess:
        seen["argv"] = list(argv)
        seen["grok_home"] = kw.get("grok_home")
        out = Path(kw["stdout_path"])
        err = Path(kw["stderr_path"])
        out.write_text("", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return SpawnedProcess(pid=99901, pgid=99901, stdout_path=out, stderr_path=err)

    monkeypatch.setattr(gb_mod, "spawn_grok", fake_spawn)
    result = grok_build(
        {"mode": "design", "prompt": "design a widget", "cwd": str(repo)},
        ctx,
    )
    assert result.ok
    assert result.payload.get("status") == "running"
    job_id = result.payload.get("job_id")
    assert job_id
    meta = load_job(ctx.paths, job_id)
    assert meta is not None
    assert meta.pid == 99901
    assert meta.mode == "design"
    assert meta.async_job is True
    # Design slash in -p body
    assert any("/design" in a or a.startswith("/design") for a in seen.get("argv", [])) or any(
        "/design" in str(a) for a in seen.get("argv", [])
    )


def test_poll_job_running(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)

    def fake_spawn(argv, **kw: Any) -> SpawnedProcess:
        out = Path(kw["stdout_path"])
        err = Path(kw["stderr_path"])
        out.write_text("", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return SpawnedProcess(pid=888, pgid=888, stdout_path=out, stderr_path=err)

    monkeypatch.setattr(gb_mod, "spawn_grok", fake_spawn)
    spawned = grok_build(
        {"mode": "review", "cwd": str(repo), "target": "local"},
        ctx,
    )
    assert spawned.ok
    job_id = spawned.payload["job_id"]
    polled = grok_build({"mode": "review", "job_id": job_id}, ctx)
    assert polled.ok
    assert polled.payload.get("status") == "running"
    assert polled.payload.get("job_id") == job_id


def test_poll_job_not_found(ctx: ToolContext) -> None:
    result = grok_build({"mode": "prompt", "job_id": "does-not-exist-zzzz"}, ctx)
    assert not result.ok
    assert result.error_reason == "job_not_found"


def test_invalid_effort(
    ctx: ToolContext,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ready(monkeypatch)
    result = grok_build(
        {"mode": "implement", "prompt": "x", "effort": 99, "cwd": str(repo)},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "invalid_effort"


def test_source_never_assigns_secret_env() -> None:
    """Static guard: handler source must not write secret_env with access."""
    src = Path(gb_mod.__file__).read_text(encoding="utf-8")
    # No assignment / mutation of secret_env (mentions in docstrings OK).
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
            continue
        if "secret_env" not in stripped:
            continue
        # Allow docstring/comment mentions of the law; forbid mutation.
        assert "=" not in stripped or "never" in stripped.lower() or "Law:" in stripped
        assert "secret_env] =" not in stripped
        assert 'secret_env"] =' not in stripped
        assert "secret_env'] =" not in stripped


def test_runner_entry_resolves() -> None:
    from elyra.tools.builtin.grok_build import grok_build as entry

    assert callable(entry)
