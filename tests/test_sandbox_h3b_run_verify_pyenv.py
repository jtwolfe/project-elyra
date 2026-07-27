"""H3b / PR5: guest run (KD24), verify guest pytest (KD22), curated pyenv."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.sandbox import (
    FakeSandboxClient,
    Sandbox,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    set_sandbox_lifecycle,
)
from elyra.sandbox.paths import ENV_ELYRA_SANDBOX, PRIMARY_NAME, ensure_host_tree
from elyra.sandbox.protocol import ExecResult
from elyra.sandbox.pyenv import (
    GUEST_REQUIREMENTS_PATH,
    ensure_pyenv_marker_for_tests,
    guest_pip_install_argv,
    needs_pyenv_install,
    pyenv_ready,
    requirements_file,
    try_install_curated_pyenv,
    write_pyenv_marker,
)
from elyra.sandbox.status import sandbox_status_block
from elyra.tools.builtin import run_cmd
from elyra.tools.builtin.growth import install_tool_draft, verify_tool
from elyra.tools.guest_exec import (
    EXECUTOR_BACKEND_HOST_STUB,
    EXECUTOR_BACKEND_MICROSANDBOX,
)
from elyra.tools.types import ToolContext
from elyra.tools.verify import (
    VERIFY_RECORD_NAME,
    load_verify_record,
    verify_draft_tool,
    verify_stage_dir,
)


@pytest.fixture(autouse=True)
def _clear_lifecycle():
    clear_sandbox_lifecycle()
    yield
    clear_sandbox_lifecycle()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _minimal_draft_files(
    *,
    test_body: str = "def test_ok():\n    assert True\n",
) -> dict[str, str]:
    return {
        "TOOL.md": (
            "---\nname: sample_tool\ndescription: sample\nkind: read\n---\n\n# sample\n"
        ),
        "schema.json": json.dumps(
            {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        "runner.json": json.dumps(
            {"kind": "sandbox_python", "module": "impl/main.py", "function": "run"}
        ),
        "impl/main.py": "def run(args):\n    return {'ok': True}\n",
        "tests/test_sample.py": test_body,
    }


def _install_draft(ctx: ToolContext, name: str, files: dict[str, str] | None = None):
    payload = files if files is not None else _minimal_draft_files()
    return install_tool_draft({"name": name, "files": payload}, ctx)


# ---------------------------------------------------------------------------
# Curated requirements seed + pyenv marker
# ---------------------------------------------------------------------------


def test_requirements_curated_includes_pytest() -> None:
    """Repo seed must list pytest for isolation-on verify (KD22)."""
    from elyra.config import project_root

    req = project_root() / "sandboxes" / "sandbox0" / "lib" / "requirements-curated.txt"
    assert req.is_file(), "requirements-curated.txt must ship in repo seed"
    text = req.read_text(encoding="utf-8").lower()
    assert "pytest" in text


def test_requirements_curated_avoids_compile_heavy_lxml() -> None:
    """Guest often lacks gcc/libxml2; curated list must not require lxml builds."""
    from elyra.config import project_root

    req = project_root() / "sandboxes" / "sandbox0" / "lib" / "requirements-curated.txt"
    text = req.read_text(encoding="utf-8")
    # Allow comments mentioning lxml as a negative example; ban a real pin.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not stripped.lower().startswith("lxml"), (
            f"curated requirements must not pin lxml (wheel/build risk): {stripped!r}"
        )


def test_guest_pip_argv_prefers_binary() -> None:
    from elyra.sandbox.pyenv import guest_pip_install_argv

    argv = guest_pip_install_argv()
    assert "--prefer-binary" in argv
    assert "-r" in argv


def test_seed_copies_requirements_into_host_tree(paths) -> None:
    root = ensure_host_tree(PRIMARY_NAME, paths)
    req = requirements_file(root)
    assert req.is_file()
    assert "pytest" in req.read_text(encoding="utf-8").lower()


def test_pyenv_marker_helpers(paths) -> None:
    root = ensure_host_tree(PRIMARY_NAME, paths)
    assert pyenv_ready(root) is False
    assert needs_pyenv_install(root) is True
    write_pyenv_marker(root)
    assert pyenv_ready(root) is True
    assert needs_pyenv_install(root) is False


def test_try_install_curated_pyenv_fake_guest(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake guest pip success writes marker (warm path)."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    # pip install success
    sb.default_exec = ExecResult(exit_code=0, stdout_text="Successfully installed\n")
    root = ensure_host_tree(PRIMARY_NAME, paths)
    assert pyenv_ready(root) is False
    result = try_install_curated_pyenv(life, paths=paths)
    assert result.ok is True
    assert result.error_reason is None
    assert result.requirements_hash is not None
    assert pyenv_ready(root) is True
    assert client.last_exec is not None
    assert client.last_exec["cmd"] == "python3"
    args = client.last_exec.get("args") or []
    assert "-m" in args and "pip" in args
    assert GUEST_REQUIREMENTS_PATH in args


def test_try_install_skips_when_pip_fails(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    sb.default_exec = ExecResult(exit_code=1, stderr_text="no network\n")
    root = ensure_host_tree(PRIMARY_NAME, paths)
    result = try_install_curated_pyenv(life, paths=paths)
    assert result.ok is False
    assert result.error_reason == "pip_failed"
    assert result.exit_code == 1
    assert "no network" in (result.stderr_tail or "")
    assert pyenv_ready(root) is False


# ---------------------------------------------------------------------------
# Builtin run — guest when isolation on (KD24)
# ---------------------------------------------------------------------------


def test_run_host_stub_when_isolation_off(paths, home: Path) -> None:
    """ELYRA_SANDBOX=0 (conftest default): host Sandbox.run."""
    sb = Sandbox(paths)
    ctx = ToolContext(paths=paths, sandbox=sb)
    result = run_cmd.run(
        {"command": ["python3", "-c", "print('host-hi')"]},
        ctx,
    )
    assert result.ok is True
    assert "host-hi" in result.payload["stdout"]
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB


def test_run_guest_when_isolation_on(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None
    sb_conn.default_exec = ExecResult(exit_code=0, stdout_text="guest-hi\n")

    # Host sandbox still required only for isolation-off; guest path ignores it.
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": ["echo", "guest-hi"]}, ctx)
    assert result.ok is True
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    assert result.payload.get("returncode") == 0
    assert "guest-hi" in result.payload["stdout"]
    assert client.last_exec is not None
    assert client.last_exec["cmd"] == "echo"
    assert client.last_exec.get("cwd") == "/workspace"


def test_run_guest_fail_closed_no_lifecycle(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    clear_sandbox_lifecycle()
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": ["echo", "x"]}, ctx)
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("sandbox_unavailable")
    assert result.payload.get("isolation") is True


def test_run_guest_fail_closed_client_unusable(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life = SandboxLifecycleManager(paths=paths, client_unusable=True)
    set_sandbox_lifecycle(life)
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": ["echo", "x"]}, ctx)
    assert result.ok is False
    assert "client_unusable" in (result.error_reason or "")


def test_run_guest_nonzero_still_ok(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-zero exit is payload data, not infrastructure failure (host parity)."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None
    sb_conn.default_exec = ExecResult(exit_code=7, stderr_text="boom\n")
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": ["false"]}, ctx)
    assert result.ok is True
    assert result.payload["returncode"] == 7


def test_run_command_too_large_guest(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guest pre-exec gate rejects commands over 16 KiB with soft FS hint."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    huge = "x" * (17 * 1024)
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": huge}, ctx)
    assert result.ok is False
    assert result.error_reason == "command_too_large"
    assert result.payload.get("limit_bytes") == 16384
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    hint = result.payload.get("hint") or ""
    assert "search_replace" in hint
    assert "Path.write_text" in hint
    assert "install_tool_draft" in hint
    # Must not have reached guest exec
    assert client.last_exec is None


def test_run_command_at_guest_cap_not_rejected(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Command of exactly 16 KiB is not rejected at the pre-exec size gate."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None
    sb_conn.default_exec = ExecResult(exit_code=0, stdout_text="ok\n")
    at_cap = "x" * (16 * 1024)
    assert len(at_cap.encode("utf-8")) == run_cmd._GUEST_MAX_COMMAND_BYTES
    ctx = ToolContext(paths=paths, sandbox=Sandbox(paths))
    result = run_cmd.run({"command": at_cap}, ctx)
    assert result.error_reason != "command_too_large"
    assert result.ok is True
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    assert client.last_exec is not None


# ---------------------------------------------------------------------------
# verify_tool — host pytest off / guest pytest on
# ---------------------------------------------------------------------------


def test_verify_host_pytest_when_isolation_off(paths) -> None:
    ctx = ToolContext(paths=paths)
    name = "host_verify"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is True, result
    assert result.payload.get("passed") is True
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB
    stage = verify_stage_dir(paths, name)
    assert stage.is_dir()
    assert (stage / "tests").is_dir()
    host = ensure_host_tree(PRIMARY_NAME, paths)
    assert stage.resolve().is_relative_to(host.resolve())
    draft = paths.tools_dir / "drafts" / name
    rec = load_verify_record(draft)
    assert rec is not None
    assert rec.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB


def test_verify_guest_pytest_unavailable_without_pyenv(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    # Intentionally no pyenv marker
    root = ensure_host_tree(PRIMARY_NAME, paths)
    assert pyenv_ready(root) is False

    ctx = ToolContext(paths=paths)
    name = "no_pyenv"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "guest_pytest_unavailable"
    draft = paths.tools_dir / "drafts" / name
    assert not (draft / VERIFY_RECORD_NAME).exists()


def test_verify_guest_pytest_with_fake(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    ensure_pyenv_marker_for_tests(paths)
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None
    # Guest smoke (exit 0) then pytest success — both use default_exec
    sb_conn.default_exec = ExecResult(exit_code=0, stdout_text="1 passed\n")
    exec_log: list[dict] = []
    _orig_exec = sb_conn.exec

    async def _tracking_exec(cmd, args=None, **kwargs):
        result = await _orig_exec(cmd, args, **kwargs)
        exec_log.append(
            {
                "cmd": cmd,
                "args": list(args or []),
                "cwd": kwargs.get("cwd"),
            }
        )
        return result

    sb_conn.exec = _tracking_exec  # type: ignore[method-assign]

    ctx = ToolContext(paths=paths)
    name = "guest_verify"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is True, result
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    # KD-G6: smoke (-c) then pytest (-m pytest), both under .verify/
    assert len(exec_log) >= 2
    smoke_call = exec_log[0]
    assert smoke_call["cmd"] == "python3"
    assert "-c" in smoke_call["args"]
    assert smoke_call["cwd"] == f"/workspace/tools/.verify/{name}"
    smoke_src = smoke_call["args"][smoke_call["args"].index("-c") + 1]
    assert "_elyra_verify_smoke" in smoke_src
    assert f"/workspace/tools/.verify/{name}/" in smoke_src
    pytest_call = exec_log[-1]
    assert pytest_call["cmd"] == "python3"
    assert "-m" in pytest_call["args"] and "pytest" in pytest_call["args"]
    assert pytest_call["cwd"] == f"/workspace/tools/.verify/{name}"
    assert client.last_exec is not None
    assert client.last_exec["cmd"] == "python3"
    args = client.last_exec.get("args") or []
    assert "-m" in args and "pytest" in args
    assert client.last_exec.get("cwd") == f"/workspace/tools/.verify/{name}"
    stage = verify_stage_dir(paths, name)
    assert stage.is_dir()
    draft = paths.tools_dir / "drafts" / name
    rec = load_verify_record(draft)
    assert rec is not None and rec["passed"] is True


def test_verify_guest_smoke_module_missing_fail_closed(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2 from guest smoke → verify_guest_module_missing; no .verify.json."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    ensure_pyenv_marker_for_tests(paths)
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None
    calls = {"smoke": 0, "pytest": 0}

    async def _smoke_missing(cmd, args=None, **kwargs):
        del cmd, kwargs
        args = list(args or [])
        if "-c" in args:
            calls["smoke"] += 1
            return ExecResult(exit_code=2, stderr_text="missing on guest\n")
        calls["pytest"] += 1
        return ExecResult(exit_code=0, stdout_text="1 passed\n")

    sb_conn.exec = _smoke_missing  # type: ignore[method-assign]

    ctx = ToolContext(paths=paths)
    name = "smoke_missing"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_guest_module_missing"
    draft = paths.tools_dir / "drafts" / name
    assert not (draft / VERIFY_RECORD_NAME).exists()
    # Must not reach pytest when smoke fails
    assert calls["smoke"] == 1
    assert calls["pytest"] == 0


def test_verify_guest_smoke_import_failed_fail_closed(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-2/3 exit from guest smoke → verify_guest_module_import_failed."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    ensure_pyenv_marker_for_tests(paths)
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None

    async def _smoke_import_boom(cmd, args=None, **kwargs):
        del cmd, kwargs
        args = list(args or [])
        if "-c" in args:
            return ExecResult(
                exit_code=1,
                stderr_text="Traceback (most recent call last):\nImportError: boom\n",
            )
        return ExecResult(exit_code=0, stdout_text="1 passed\n")

    sb_conn.exec = _smoke_import_boom  # type: ignore[method-assign]

    ctx = ToolContext(paths=paths)
    name = "smoke_import_fail"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_guest_module_import_failed"
    assert "ImportError" in (result.payload.get("log") or "")
    draft = paths.tools_dir / "drafts" / name
    assert not (draft / VERIFY_RECORD_NAME).exists()


def test_verify_guest_smoke_function_not_found_fail_closed(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 3 from guest smoke → verify_guest_function_not_found."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    ensure_pyenv_marker_for_tests(paths)
    sb_conn = life.get_connected(PRIMARY_NAME)
    assert sb_conn is not None

    async def _smoke_no_fn(cmd, args=None, **kwargs):
        del cmd, kwargs
        args = list(args or [])
        if "-c" in args:
            # Non-empty stderr required: empty non-zero is treated as sandbox death.
            return ExecResult(exit_code=3, stderr_text="function missing\n")
        return ExecResult(exit_code=0, stdout_text="1 passed\n")

    sb_conn.exec = _smoke_no_fn  # type: ignore[method-assign]

    ctx = ToolContext(paths=paths)
    name = "smoke_no_fn"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_guest_function_not_found"
    draft = paths.tools_dir / "drafts" / name
    assert not (draft / VERIFY_RECORD_NAME).exists()


def test_verify_guest_pytest_fail_closed_unusable(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life = SandboxLifecycleManager(paths=paths, client_unusable=True)
    set_sandbox_lifecycle(life)
    ensure_pyenv_marker_for_tests(paths)
    ctx = ToolContext(paths=paths)
    name = "unusable_verify"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert (result.error_reason or "").startswith("sandbox_unavailable")


def test_verify_draft_tool_direct_host_backend(paths) -> None:
    """verify_draft_tool records host_stub backend when isolation off."""
    name = "direct_host"
    draft = paths.tools_dir / "drafts" / name
    draft.mkdir(parents=True)
    for rel, body in _minimal_draft_files().items():
        p = draft / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    out = verify_draft_tool(paths, name)
    assert out["ok"] is True
    assert out["executor_backend"] == EXECUTOR_BACKEND_HOST_STUB


# ---------------------------------------------------------------------------
# Status ready semantics after H3b
# ---------------------------------------------------------------------------


def test_status_ready_requires_pyenv(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    block = sandbox_status_block(paths)
    assert block["mount_ready"] is True
    assert block["pyenv_ready"] is False
    assert block["ready"] is False
    assert block["reason"] == "pyenv_not_ready"

    ensure_pyenv_marker_for_tests(paths)
    block2 = sandbox_status_block(paths)
    assert block2["pyenv_ready"] is True
    assert block2["ready"] is True
    assert block2["reason"] is None


def test_guest_pip_argv_points_at_workspace_lib() -> None:
    argv = guest_pip_install_argv()
    assert argv[:3] == ["-m", "pip", "install"]
    assert "--user" in argv
    assert GUEST_REQUIREMENTS_PATH in argv
