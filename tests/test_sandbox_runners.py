"""H3a / PR4: sandbox_python + sandbox_shell dispatch (host stub + Fake guest)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.sandbox import (
    FakeSandboxClient,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    set_sandbox_lifecycle,
)
from elyra.sandbox.paths import ENV_ELYRA_SANDBOX, PRIMARY_NAME, ensure_host_tree
from elyra.sandbox.protocol import ExecResult
from elyra.tools.guest_exec import (
    EXECUTOR_BACKEND_HOST_STUB,
    EXECUTOR_BACKEND_MICROSANDBOX,
    ENV_TOOL_ARGS,
    map_python_exec_result,
    stage_package_for_guest,
)
from elyra.tools.registry import ToolRegistry
from elyra.tools.runner import RunnerSpec, dispatch, load_runner_json, validate_runner_fields
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.verify import validate_draft_package


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture(autouse=True)
def _clear_lifecycle():
    clear_sandbox_lifecycle()
    yield
    clear_sandbox_lifecycle()


def _write_sandbox_python_pkg(
    root: Path,
    name: str,
    *,
    module_rel: str = "impl/main.py",
    function: str = "run",
    body: str | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "TOOL.md").write_text(
        f"---\nname: {name}\ndescription: py tool\nkind: read\n---\n",
        encoding="utf-8",
    )
    (pkg / "schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            }
        ),
        encoding="utf-8",
    )
    runner: dict = {"kind": "sandbox_python", "module": module_rel}
    if function != "run":
        runner["function"] = function
    else:
        runner["function"] = function
    (pkg / "runner.json").write_text(json.dumps(runner), encoding="utf-8")
    mod_path = pkg / module_rel
    mod_path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = (
            "def run(args):\n"
            "    text = (args or {}).get('text', '')\n"
            "    return {'ok': True, 'upper': str(text).upper()}\n"
        )
    mod_path.write_text(body, encoding="utf-8")
    return pkg


def _write_sandbox_shell_pkg(
    root: Path,
    name: str,
    *,
    argv: list[str] | None = None,
    script: str | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "TOOL.md").write_text(
        f"---\nname: {name}\ndescription: shell tool\nkind: read\n---\n",
        encoding="utf-8",
    )
    (pkg / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {"msg": {"type": "string"}}}),
        encoding="utf-8",
    )
    if argv is None:
        argv = ["python3", "-B", "impl/cli.py"]
    (pkg / "runner.json").write_text(
        json.dumps({"kind": "sandbox_shell", "argv": argv}),
        encoding="utf-8",
    )
    if script is None:
        script = (
            "import json, os, pathlib\n"
            "p = os.environ.get('ELYRA_TOOL_ARGS')\n"
            "args = json.loads(pathlib.Path(p).read_text()) if p else {}\n"
            "print(json.dumps({'ok': True, 'msg': args.get('msg', '')}))\n"
        )
    cli = pkg / "impl" / "cli.py"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(script, encoding="utf-8")
    return pkg


# ---------------------------------------------------------------------------
# Runner shape validation
# ---------------------------------------------------------------------------


def test_load_runner_json_defaults_function(tmp_path: Path) -> None:
    pkg = _write_sandbox_python_pkg(tmp_path, "echo_upper")
    # Rewrite without function key
    data = json.loads((pkg / "runner.json").read_text(encoding="utf-8"))
    data.pop("function", None)
    (pkg / "runner.json").write_text(json.dumps(data), encoding="utf-8")
    spec = load_runner_json(pkg)
    assert spec.kind == "sandbox_python"
    assert spec.module == "impl/main.py"
    assert spec.function == "run"


def test_load_runner_json_rejects_module_dotdot(tmp_path: Path) -> None:
    pkg = _write_sandbox_python_pkg(tmp_path, "bad_mod")
    (pkg / "runner.json").write_text(
        json.dumps(
            {"kind": "sandbox_python", "module": "../escape.py", "function": "run"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid_runner:module"):
        load_runner_json(pkg)


def test_load_runner_json_rejects_absolute_module(tmp_path: Path) -> None:
    pkg = _write_sandbox_python_pkg(tmp_path, "bad_abs")
    (pkg / "runner.json").write_text(
        json.dumps(
            {"kind": "sandbox_python", "module": "/tmp/x.py", "function": "run"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid_runner:module_absolute"):
        load_runner_json(pkg)


def test_load_runner_json_rejects_private_function(tmp_path: Path) -> None:
    pkg = _write_sandbox_python_pkg(tmp_path, "bad_fn")
    (pkg / "runner.json").write_text(
        json.dumps(
            {
                "kind": "sandbox_python",
                "module": "impl/main.py",
                "function": "_private",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid_runner:function"):
        load_runner_json(pkg)


def test_load_runner_json_shell_requires_argv(tmp_path: Path) -> None:
    pkg = tmp_path / "sh"
    pkg.mkdir()
    (pkg / "runner.json").write_text(
        json.dumps({"kind": "sandbox_shell"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid_runner:argv"):
        load_runner_json(pkg)


def test_validate_draft_package_shape_errors(tmp_path: Path) -> None:
    draft = tmp_path / "draft_tool"
    draft.mkdir()
    (draft / "TOOL.md").write_text("---\nname: draft_tool\n---\n", encoding="utf-8")
    (draft / "schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (draft / "tests").mkdir()
    (draft / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (draft / "runner.json").write_text(
        json.dumps({"kind": "sandbox_python", "module": "../x.py"}),
        encoding="utf-8",
    )
    err = validate_draft_package(draft)
    assert err is not None
    assert err.startswith("invalid_runner:")

    (draft / "runner.json").write_text(
        json.dumps({"kind": "sandbox_shell", "argv": []}),
        encoding="utf-8",
    )
    err = validate_draft_package(draft)
    assert err == "invalid_runner:argv_empty"


def test_validate_runner_fields_module_missing() -> None:
    assert (
        validate_runner_fields("sandbox_python", {"kind": "sandbox_python"})
        == "invalid_runner:module_missing"
    )


# ---------------------------------------------------------------------------
# package_dir_missing + isolation fail-closed
# ---------------------------------------------------------------------------


def test_dispatch_package_dir_missing() -> None:
    runner = RunnerSpec(
        kind="sandbox_python", module="impl/main.py", function="run"
    )
    ctx = ToolContext(paths=resolve_paths())
    result = dispatch(runner, {"text": "hi"}, ctx, package_dir=None)
    assert result.ok is False
    assert result.error_reason == "package_dir_missing"


def test_guest_fail_closed_when_lifecycle_missing(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)  # isolation on
    clear_sandbox_lifecycle()
    pkg = _write_sandbox_python_pkg(tmp_path, "lonely")
    runner = load_runner_json(pkg)
    ctx = ToolContext(paths=paths)
    result = dispatch(runner, {"text": "x"}, ctx, package_dir=pkg)
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("sandbox_unavailable")
    assert result.payload.get("isolation") is True


def test_guest_fail_closed_when_client_unusable(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life = SandboxLifecycleManager(
        paths=paths, client=None, client_unusable=True, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    pkg = _write_sandbox_python_pkg(tmp_path, "unusable_tool")
    runner = load_runner_json(pkg)
    result = dispatch(
        runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg
    )
    assert result.ok is False
    assert "sandbox_unavailable" in (result.error_reason or "")
    assert result.payload.get("isolation") is True


# ---------------------------------------------------------------------------
# Host stub (ELYRA_SANDBOX=0)
# ---------------------------------------------------------------------------


def test_host_stub_python_fn_args(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    pkg = _write_sandbox_python_pkg(local, "echo_upper")
    empty_b = tmp_path / "empty_bundled"
    empty_b.mkdir(exist_ok=True)
    reg = ToolRegistry(paths, bundled_root=empty_b)
    reg.reload()
    assert reg.has("echo_upper")
    result = reg.execute(
        "echo_upper",
        {"text": "hi"},
        ToolContext(paths=paths, registry=reg),
    )
    assert result.ok is True
    assert result.payload.get("upper") == "HI"
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB


def test_host_stub_python_tool_level_not_ok(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    body = "def run(args):\n    return {'ok': False, 'reason': 'nope'}\n"
    pkg = _write_sandbox_python_pkg(tmp_path, "fail_tool", body=body)
    runner = load_runner_json(pkg)
    result = dispatch(
        runner, {}, ToolContext(paths=paths), package_dir=pkg
    )
    assert result.ok is False
    assert result.error_reason == "tool_returned_not_ok"
    assert result.payload.get("reason") == "nope"


def test_host_stub_python_handler_error(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    body = "def run(args):\n    raise RuntimeError('boom')\n"
    pkg = _write_sandbox_python_pkg(tmp_path, "boom_tool", body=body)
    runner = load_runner_json(pkg)
    result = dispatch(runner, {}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is False
    assert result.error_reason == "handler_error:RuntimeError"


def test_host_stub_shell_elyra_tool_args(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    pkg = _write_sandbox_shell_pkg(tmp_path, "shell_echo")
    runner = load_runner_json(pkg)
    result = dispatch(
        runner,
        {"msg": "hello"},
        ToolContext(paths=paths),
        package_dir=pkg,
    )
    assert result.ok is True
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB
    # CLI printed JSON; shell map is exit-based — stdout contains the JSON
    assert "hello" in (result.payload.get("stdout") or "")
    # Args file cleaned up under host tree tmp/
    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    leftovers = list((host_root / "tmp").glob("elyra_tool_args_*.json"))
    assert leftovers == []


def test_host_stub_shell_nonzero(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    pkg = _write_sandbox_shell_pkg(
        tmp_path,
        "shell_fail",
        argv=["python3", "-B", "-c", "import sys; sys.exit(3)"],
        script="",  # unused
    )
    # rewrite without impl dependency
    (pkg / "runner.json").write_text(
        json.dumps(
            {
                "kind": "sandbox_shell",
                "argv": ["python3", "-B", "-c", "import sys; sys.exit(3)"],
            }
        ),
        encoding="utf-8",
    )
    runner = load_runner_json(pkg)
    result = dispatch(runner, {}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is False
    assert result.error_reason == "host_nonzero_exit"
    assert result.payload.get("exit_code") == 3


# ---------------------------------------------------------------------------
# Return map unit tests
# ---------------------------------------------------------------------------


def test_return_map_invalid_json() -> None:
    r = map_python_exec_result(
        exit_code=0,
        stdout="not-json",
        stderr="",
        executor_backend=EXECUTOR_BACKEND_HOST_STUB,
    )
    assert r.ok is False
    assert r.error_reason == "invalid_guest_json"


def test_return_map_non_object_json() -> None:
    r = map_python_exec_result(
        exit_code=0,
        stdout="[1,2,3]",
        stderr="",
        executor_backend=EXECUTOR_BACKEND_HOST_STUB,
    )
    assert r.ok is True
    assert r.payload.get("result") == [1, 2, 3]


def test_return_map_nonzero() -> None:
    r = map_python_exec_result(
        exit_code=2,
        stdout="",
        stderr="boom",
        executor_backend=EXECUTOR_BACKEND_MICROSANDBOX,
        isolation=True,
    )
    assert r.ok is False
    assert r.error_reason == "guest_nonzero_exit"


# ---------------------------------------------------------------------------
# Atomic stage
# ---------------------------------------------------------------------------


def test_stage_package_atomic_and_excludes_pycache(paths, tmp_path: Path) -> None:
    pkg = _write_sandbox_python_pkg(tmp_path, "staged_tool")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"\0")
    (pkg / "keep.txt").write_text("yes", encoding="utf-8")

    dest = stage_package_for_guest(paths, pkg)
    assert dest.is_dir()
    assert (dest / "keep.txt").read_text(encoding="utf-8") == "yes"
    assert not (dest / "__pycache__").exists()
    # Stage work dirs cleaned
    stage_root = ensure_host_tree(PRIMARY_NAME, paths) / "tools" / ".stage"
    leftovers = [p for p in stage_root.iterdir() if p.name.startswith("staged_tool.")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Fake guest path (isolation on + FakeSandboxClient)
# ---------------------------------------------------------------------------


def test_fake_guest_python_stages_and_execs(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    # Default exec returns exit 0 with ok JSON for any key after readiness.
    client.default_exec = ExecResult(  # type: ignore[attr-defined]
        exit_code=0,
        stdout_text=json.dumps({"ok": True, "upper": "HI"}),
    )
    # FakeConnectedSandbox uses client.default path via default_exec on connected.
    life = SandboxLifecycleManager(
        paths=paths,
        client=client,
        client_unusable=False,
        skip_guest_readiness=True,
    )
    set_sandbox_lifecycle(life)
    # Ensure ready cache
    ensure_result = life.ensure(PRIMARY_NAME)
    assert ensure_result.ready

    # Configure connected default_exec
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    sb.default_exec = ExecResult(
        exit_code=0,
        stdout_text=json.dumps({"ok": True, "upper": "HI"}),
    )

    pkg = _write_sandbox_python_pkg(tmp_path, "fake_py")
    runner = load_runner_json(pkg)
    result = dispatch(
        runner, {"text": "hi"}, ToolContext(paths=paths), package_dir=pkg
    )
    assert result.ok is True
    assert result.payload.get("upper") == "HI"
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    # Staged under host tools/
    host_tools = ensure_host_tree(PRIMARY_NAME, paths) / "tools" / "fake_py"
    assert host_tools.is_dir()
    assert (host_tools / "impl" / "main.py").is_file()
    # Exec was invoked
    assert client.exec_calls >= 1
    assert client.last_exec is not None
    assert client.last_exec["cmd"] == "python3"
    assert "-c" in (client.last_exec.get("args") or [])


def test_fake_guest_shell_writes_tool_args_env(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths,
        client=client,
        skip_guest_readiness=True,
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    sb.default_exec = ExecResult(exit_code=0, stdout_text="done\n")

    pkg = _write_sandbox_shell_pkg(tmp_path, "fake_sh")
    runner = load_runner_json(pkg)
    result = dispatch(
        runner,
        {"msg": "via-env"},
        ToolContext(paths=paths),
        package_dir=pkg,
    )
    assert result.ok is True
    assert client.last_exec is not None
    env = client.last_exec.get("env") or {}
    assert ENV_TOOL_ARGS in env
    assert env[ENV_TOOL_ARGS].startswith("/workspace/tmp/elyra_tool_args_")
    assert client.last_exec.get("cwd") == "/workspace/tools/fake_sh"
    # Host args file cleaned
    leftovers = list(
        (ensure_host_tree(PRIMARY_NAME, paths) / "tmp").glob("elyra_tool_args_*.json")
    )
    assert leftovers == []


def test_fake_guest_reconnect_on_mid_exec_death(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: "running"},
        empty_fail_times=1,  # first exec empty fail → death heuristic
    )
    life = SandboxLifecycleManager(
        paths=paths,
        client=client,
        skip_guest_readiness=True,
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    sb.default_exec = ExecResult(
        exit_code=0,
        stdout_text=json.dumps({"ok": True, "recovered": True}),
    )

    pkg = _write_sandbox_python_pkg(tmp_path, "retry_tool")
    runner = load_runner_json(pkg)
    result = dispatch(
        runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg
    )
    # After one empty failure + reconnect, second exec succeeds
    assert result.ok is True
    assert result.payload.get("recovered") is True
    assert client.exec_calls >= 2


def test_registry_passes_package_dir(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    _write_sandbox_python_pkg(local, "reg_pkg")
    empty_b = tmp_path / "bundled_empty"
    empty_b.mkdir()
    reg = ToolRegistry(paths, bundled_root=empty_b)
    captured: dict = {}

    def _fake(runner, args, ctx, handler=None, package_dir=None) -> ToolResult:
        captured["package_dir"] = package_dir
        captured["kind"] = runner.kind
        return ToolResult(ok=True, payload={"seen": True})

    import elyra.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "dispatch", _fake)
    result = reg.execute("reg_pkg", {"text": "a"}, ToolContext(paths=paths))
    assert result.ok is True
    assert captured.get("kind") == "sandbox_python"
    assert captured.get("package_dir") is not None
    assert Path(captured["package_dir"]).name == "reg_pkg"
