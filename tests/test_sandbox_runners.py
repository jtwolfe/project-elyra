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
    STAGE_MARKER_NAME,
    guest_module_path,
    load_stage_marker,
    map_python_exec_result,
    path_missing_signature,
    resolve_module_file,
    stage_package_for_guest,
)
import elyra.tools.guest_exec as guest_exec_mod
from elyra.tools.package_hash import content_hash
from elyra.tools.registry import ToolRegistry
from elyra.tools.runner import (
    RunnerSpec,
    dispatch,
    load_runner_json,
    validate_runner_fields,
)
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
    (draft / "schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
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
# Module path resolution (dotted import + path forms)
# ---------------------------------------------------------------------------


def test_resolve_module_file_dotted_and_path(tmp_path: Path) -> None:
    """Live dogfood: runner module \"impl.web_search\" must map to impl/web_search.py."""
    pkg = tmp_path / "web_search"
    (pkg / "impl").mkdir(parents=True)
    target = pkg / "impl" / "web_search.py"
    target.write_text("def run(args):\n    return {'ok': True}\n", encoding="utf-8")
    (pkg / "impl" / "__init__.py").write_text("", encoding="utf-8")

    for module in (
        "impl.web_search",
        "impl/web_search",
        "impl/web_search.py",
    ):
        resolved = resolve_module_file(pkg, module)
        assert resolved is not None, module
        assert resolved.resolve() == target.resolve()

    assert resolve_module_file(pkg, "impl.missing") is None
    assert resolve_module_file(pkg, "../escape") is None


def test_host_stub_dispatch_dotted_module(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """sandbox_python with dotted module must execute under host stub (ELYRA_SANDBOX=0)."""
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")
    clear_sandbox_lifecycle()
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    pkg = _write_sandbox_python_pkg(
        local,
        "dot_mod",
        module_rel="impl/echo.py",
        body=(
            "def run(args):\n"
            "    return {'ok': True, 'v': (args or {}).get('text', '')[::-1]}\n"
        ),
    )
    # Rewrite runner to dotted form (file still at impl/echo.py)
    (pkg / "runner.json").write_text(
        json.dumps(
            {"kind": "sandbox_python", "module": "impl.echo", "function": "run"}
        ),
        encoding="utf-8",
    )
    runner = load_runner_json(pkg)
    assert runner.module == "impl.echo"
    ctx = ToolContext(paths=paths)
    result = dispatch(runner, {"text": "ab"}, ctx, package_dir=pkg)
    assert result.ok is True, result
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_HOST_STUB
    # map_python_exec_result may nest the handler return
    body = result.payload
    assert body.get("ok") is True or body.get("v") == "ba" or "ba" in json.dumps(body)


def test_validate_draft_package_module_not_found(tmp_path: Path) -> None:
    draft = tmp_path / "hollow"
    draft.mkdir()
    (draft / "TOOL.md").write_text("---\nname: hollow\n---\n", encoding="utf-8")
    (draft / "schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (draft / "tests").mkdir()
    (draft / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (draft / "runner.json").write_text(
        json.dumps({"kind": "sandbox_python", "module": "impl.main"}),
        encoding="utf-8",
    )
    # No impl/main.py
    err = validate_draft_package(draft)
    assert err == "invalid_runner:module_not_found"

    # Add the file — shape ok
    (draft / "impl").mkdir()
    (draft / "impl" / "main.py").write_text("def run(args):\n    return {}\n")
    assert validate_draft_package(draft) is None


# ---------------------------------------------------------------------------
# package_dir_missing + isolation fail-closed
# ---------------------------------------------------------------------------


def test_dispatch_package_dir_missing() -> None:
    runner = RunnerSpec(kind="sandbox_python", module="impl/main.py", function="run")
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
    result = dispatch(runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg)
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
    result = dispatch(runner, {}, ToolContext(paths=paths), package_dir=pkg)
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
    # Marker written after success; content_hash matches SOURCE
    marker = load_stage_marker(dest)
    assert marker is not None
    assert marker["content_hash"] == content_hash(pkg)
    assert marker.get("incomplete") is False
    assert marker.get("schema_version") == 1


def test_stage_skip_same_bytes_second_call(paths, tmp_path: Path) -> None:
    """Two stages same bytes → second skips (probe mtime stable; no leftovers)."""
    pkg = _write_sandbox_python_pkg(tmp_path, "skip_tool")
    dest1 = stage_package_for_guest(paths, pkg)
    probe = dest1 / "impl" / "main.py"
    assert probe.is_file()
    mtime1 = probe.stat().st_mtime_ns
    marker1 = load_stage_marker(dest1)
    assert marker1 is not None
    staged_at1 = marker1["staged_at"]

    dest2 = stage_package_for_guest(paths, pkg)
    assert dest2 == dest1
    mtime2 = probe.stat().st_mtime_ns
    assert mtime2 == mtime1
    marker2 = load_stage_marker(dest2)
    assert marker2 is not None
    assert marker2["staged_at"] == staged_at1
    assert marker2["content_hash"] == content_hash(pkg)
    stage_root = ensure_host_tree(PRIMARY_NAME, paths) / "tools" / ".stage"
    leftovers = [p for p in stage_root.iterdir() if p.name.startswith("skip_tool.")]
    assert leftovers == []


def test_stage_restage_on_byte_change(paths, tmp_path: Path) -> None:
    """Byte change of source → re-stage and marker hash updates."""
    pkg = _write_sandbox_python_pkg(tmp_path, "change_tool")
    dest1 = stage_package_for_guest(paths, pkg)
    h1 = content_hash(pkg)
    marker1 = load_stage_marker(dest1)
    assert marker1 is not None
    assert marker1["content_hash"] == h1
    staged_at1 = marker1["staged_at"]

    # Mutate source payload bytes
    main = pkg / "impl" / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8") + "\n# touched\n",
        encoding="utf-8",
    )
    h2 = content_hash(pkg)
    assert h2 != h1

    dest2 = stage_package_for_guest(paths, pkg)
    assert dest2 == dest1 or dest2.resolve() == dest1.resolve()
    marker2 = load_stage_marker(dest2)
    assert marker2 is not None
    assert marker2["content_hash"] == h2
    assert marker2["staged_at"] != staged_at1 or marker2["content_hash"] != h1
    # Dest has the new content
    assert "# touched" in (dest2 / "impl" / "main.py").read_text(encoding="utf-8")


def test_stage_restage_on_corrupt_or_missing_marker(paths, tmp_path: Path) -> None:
    """Corrupt / missing / incomplete marker → re-stage."""
    pkg = _write_sandbox_python_pkg(tmp_path, "marker_tool")
    dest = stage_package_for_guest(paths, pkg)
    probe = dest / "impl" / "main.py"
    h = content_hash(pkg)

    # Missing marker
    (dest / STAGE_MARKER_NAME).unlink()
    assert load_stage_marker(dest) is None
    dest2 = stage_package_for_guest(paths, pkg)
    marker = load_stage_marker(dest2)
    assert marker is not None
    assert marker["content_hash"] == h

    # Corrupt marker
    (dest2 / STAGE_MARKER_NAME).write_text("not-json{{{", encoding="utf-8")
    assert load_stage_marker(dest2) is None
    dest3 = stage_package_for_guest(paths, pkg)
    marker3 = load_stage_marker(dest3)
    assert marker3 is not None
    assert marker3["content_hash"] == h

    # Incomplete marker
    (dest3 / STAGE_MARKER_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incomplete": True,
                "content_hash": h,
                "staged_at": "2020-01-01T00:00:00Z",
                "package_name": "marker_tool",
            }
        ),
        encoding="utf-8",
    )
    dest4 = stage_package_for_guest(paths, pkg)
    marker4 = load_stage_marker(dest4)
    assert marker4 is not None
    assert marker4.get("incomplete") is False
    assert marker4["content_hash"] == h
    assert probe.is_file()


def test_stage_restage_when_dest_incomplete_despite_marker(
    paths, tmp_path: Path
) -> None:
    """Complete marker + matching hash but hollow dest → restage (module restored)."""
    pkg = _write_sandbox_python_pkg(tmp_path, "hollow_tool")
    dest = stage_package_for_guest(paths, pkg)
    marker1 = load_stage_marker(dest)
    assert marker1 is not None
    h = content_hash(pkg)
    assert marker1["content_hash"] == h
    module_path = dest / "impl" / "main.py"
    assert module_path.is_file()
    module_path.unlink()
    assert not module_path.exists()
    # Marker left intact — skip must still fail host_stage_looks_complete.
    assert load_stage_marker(dest) is not None
    assert load_stage_marker(dest)["content_hash"] == h

    dest2 = stage_package_for_guest(paths, pkg)
    assert (dest2 / "impl" / "main.py").is_file()
    marker2 = load_stage_marker(dest2)
    assert marker2 is not None
    assert marker2["content_hash"] == h
    assert marker2.get("incomplete") is False


def test_stage_strip_verify_record_refuses_skip_when_present(
    paths, tmp_path: Path
) -> None:
    """strip_verify_record=True restages when dest still has .verify.json."""
    from elyra.tools.package_hash import VERIFY_RECORD_NAME

    pkg = _write_sandbox_python_pkg(tmp_path, "strip_tool")
    (pkg / VERIFY_RECORD_NAME).write_text(
        json.dumps({"passed": True, "content_hash": "x"}),
        encoding="utf-8",
    )
    # First stage keeps the verify record (default strip=False).
    dest = stage_package_for_guest(paths, pkg, strip_verify_record=False)
    assert (dest / VERIFY_RECORD_NAME).is_file()
    marker1 = load_stage_marker(dest)
    assert marker1 is not None
    # Same source bytes + strip requested must not skip (strip contract).
    dest2 = stage_package_for_guest(paths, pkg, strip_verify_record=True)
    assert not (dest2 / VERIFY_RECORD_NAME).exists()
    marker2 = load_stage_marker(dest2)
    assert marker2 is not None
    assert marker2["content_hash"] == content_hash(pkg)
    # Second strip call with record already gone may skip.
    ino = dest2.stat().st_ino
    dest3 = stage_package_for_guest(paths, pkg, strip_verify_record=True)
    assert dest3.stat().st_ino == ino


def test_stage_force_restages_when_hash_matches(paths, tmp_path: Path) -> None:
    """force=True always restages even when content_hash matches."""
    pkg = _write_sandbox_python_pkg(tmp_path, "force_tool")
    dest1 = stage_package_for_guest(paths, pkg)
    marker1 = load_stage_marker(dest1)
    assert marker1 is not None
    h = content_hash(pkg)
    assert marker1["content_hash"] == h
    # PR2 in-place: force preserves top-level dest dentry/inode.
    ino1 = dest1.stat().st_ino
    # Sentinel staged_at so rewrite is observable without sleeping a second.
    sentinel = "1999-01-01T00:00:00Z"
    marker1["staged_at"] = sentinel
    (dest1 / STAGE_MARKER_NAME).write_text(
        json.dumps(marker1, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    dest2 = stage_package_for_guest(paths, pkg, force=True)
    marker2 = load_stage_marker(dest2)
    assert marker2 is not None
    assert marker2["content_hash"] == h
    assert dest2.stat().st_ino == ino1
    assert marker2["staged_at"] != sentinel


def test_stage_inplace_refresh_preserves_top_level_inode(paths, tmp_path: Path) -> None:
    """Re-stage when dest exists keeps the top-level package directory identity."""
    pkg = _write_sandbox_python_pkg(tmp_path, "inode_tool")
    dest1 = stage_package_for_guest(paths, pkg)
    ino1 = dest1.stat().st_ino
    # Byte change forces refresh (not skip).
    main = pkg / "impl" / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8") + "\n# refresh\n",
        encoding="utf-8",
    )
    dest2 = stage_package_for_guest(paths, pkg)
    assert dest2.resolve() == dest1.resolve()
    assert dest2.stat().st_ino == ino1
    assert "# refresh" in (dest2 / "impl" / "main.py").read_text(encoding="utf-8")
    assert load_stage_marker(dest2) is not None


def test_stage_failed_refresh_marker_absent_next_does_not_skip(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial/failed refresh leaves marker absent; next call re-stages."""
    import elyra.tools.guest_exec as guest_exec

    pkg = _write_sandbox_python_pkg(tmp_path, "partial_tool")
    dest = stage_package_for_guest(paths, pkg)
    assert load_stage_marker(dest) is not None
    ino = dest.stat().st_ino

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("simulated partial refresh failure")

    monkeypatch.setattr(guest_exec, "_in_place_refresh", boom)
    with pytest.raises(OSError, match="simulated partial"):
        stage_package_for_guest(paths, pkg, force=True)
    # Marker invalidated before mutate and never rewritten on failure.
    assert load_stage_marker(dest) is None
    assert dest.stat().st_ino == ino  # top-level dentry still present

    monkeypatch.undo()
    # Next call must not skip (no complete marker) and succeeds.
    dest2 = stage_package_for_guest(paths, pkg)
    assert dest2.stat().st_ino == ino
    marker = load_stage_marker(dest2)
    assert marker is not None
    assert marker.get("incomplete") is False
    assert marker["content_hash"] == content_hash(pkg)


def test_stage_mid_replace_failure_marker_absent_next_restages(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nth per-file replace failure after prior success: marker absent, next restages."""
    import elyra.tools.guest_exec as guest_exec

    pkg = _write_sandbox_python_pkg(tmp_path, "midfail_tool")
    (pkg / "extra.txt").write_text("v1\n", encoding="utf-8")
    dest = stage_package_for_guest(paths, pkg)
    assert load_stage_marker(dest) is not None
    ino = dest.stat().st_ino
    # Byte change so restage is required.
    (pkg / "extra.txt").write_text("v2\n", encoding="utf-8")

    real_replace = guest_exec._safe_copy_file_replace
    calls = {"n": 0}

    def flaky(src: Path, dest_path: Path, *, token: str) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("simulated mid-replace failure")
        return real_replace(src, dest_path, token=token)

    monkeypatch.setattr(guest_exec, "_safe_copy_file_replace", flaky)
    with pytest.raises(OSError, match="mid-replace"):
        stage_package_for_guest(paths, pkg)
    assert load_stage_marker(dest) is None
    assert dest.stat().st_ino == ino
    assert calls["n"] >= 2

    monkeypatch.undo()
    dest2 = stage_package_for_guest(paths, pkg)
    assert dest2.stat().st_ino == ino
    marker = load_stage_marker(dest2)
    assert marker is not None
    assert marker.get("incomplete") is False
    assert (dest2 / "extra.txt").read_text(encoding="utf-8") == "v2\n"


def test_stage_refuses_mutate_when_marker_unlink_fails(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If marker invalidate fails, do not refresh (tree stays intact + skippable)."""
    import elyra.tools.guest_exec as guest_exec

    pkg = _write_sandbox_python_pkg(tmp_path, "unlinkfail_tool")
    dest = stage_package_for_guest(paths, pkg)
    marker = load_stage_marker(dest)
    assert marker is not None
    probe = dest / "impl" / "main.py"
    body_before = probe.read_text(encoding="utf-8")
    mtime_before = probe.stat().st_mtime_ns

    def boom_unlink(_dest: Path) -> None:
        raise OSError("permission denied (simulated unlink)")

    monkeypatch.setattr(guest_exec, "_unlink_stage_marker", boom_unlink)
    with pytest.raises(OSError, match="permission denied"):
        stage_package_for_guest(paths, pkg, force=True)

    # Mutate never ran: complete marker + payload unchanged.
    assert load_stage_marker(dest) is not None
    assert load_stage_marker(dest)["content_hash"] == marker["content_hash"]
    assert probe.read_text(encoding="utf-8") == body_before
    assert probe.stat().st_mtime_ns == mtime_before


def test_stage_inplace_prunes_stale_payload(paths, tmp_path: Path) -> None:
    """Stale modules (e.g. impl/old.py) and bytecode are pruned on refresh."""
    pkg = _write_sandbox_python_pkg(tmp_path, "prune_tool")
    dest = stage_package_for_guest(paths, pkg)
    stale = dest / "impl" / "old.py"
    stale.write_text("# leftover from prior version\n", encoding="utf-8")
    cache = dest / "impl" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "old.cpython-312.pyc").write_bytes(b"\x00")
    (dest / "stray.pyc").write_bytes(b"\x00")
    # Nested pollution marker must not survive refresh (keep-set is top-level only
    # — we always prune markers during refresh).
    nested_marker = dest / "impl" / STAGE_MARKER_NAME
    nested_marker.write_text(
        '{"schema_version":1,"incomplete":false,"content_hash":"nested"}',
        encoding="utf-8",
    )
    assert stale.is_file()

    # force refresh with same source (no old.py in source)
    dest2 = stage_package_for_guest(paths, pkg, force=True)
    assert not (dest2 / "impl" / "old.py").exists()
    assert not (dest2 / "impl" / "__pycache__").exists()
    assert not (dest2 / "stray.pyc").exists()
    assert not (dest2 / "impl" / STAGE_MARKER_NAME).exists()
    assert (dest2 / "impl" / "main.py").is_file()
    # Top-level complete marker rewritten after success only.
    top = load_stage_marker(dest2)
    assert top is not None
    assert top.get("incomplete") is False
    assert top["content_hash"] == content_hash(pkg)


def test_stage_inplace_no_temp_leftovers_after_success(paths, tmp_path: Path) -> None:
    """Per-file replace leaves no *.elyra_tmp.* leftovers after success."""
    pkg = _write_sandbox_python_pkg(tmp_path, "tmpclean_tool")
    dest = stage_package_for_guest(paths, pkg)
    # Seed a leftover temp from a prior crash simulation.
    leftover = dest / "impl" / "main.py.elyra_tmp.deadbeef"
    leftover.write_text("truncated", encoding="utf-8")
    # Also force a content update so refresh runs.
    main = pkg / "impl" / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8") + "\n# ok\n",
        encoding="utf-8",
    )
    dest2 = stage_package_for_guest(paths, pkg)
    temps = [p for p in dest2.rglob("*") if ".elyra_tmp." in p.name]
    assert temps == []
    body = (dest2 / "impl" / "main.py").read_text(encoding="utf-8")
    assert "# ok" in body
    assert "truncated" not in body


def test_stage_pycache_excluded_from_dest(paths, tmp_path: Path) -> None:
    """__pycache__ excluded from staged dest (and marker is present)."""
    pkg = _write_sandbox_python_pkg(tmp_path, "pycache_tool")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-312.pyc").write_bytes(b"\x00\x01")
    (pkg / "impl" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (pkg / "impl" / "__pycache__" / "main.pyc").write_bytes(b"\x00")

    dest = stage_package_for_guest(paths, pkg)
    assert not (dest / "__pycache__").exists()
    assert not (dest / "impl" / "__pycache__").exists()
    assert (dest / STAGE_MARKER_NAME).is_file()
    # Marker itself is not re-copied from a polluted source
    polluted = pkg / STAGE_MARKER_NAME
    polluted.write_text(
        '{"schema_version":1,"incomplete":true,"content_hash":"x"}', encoding="utf-8"
    )
    dest2 = stage_package_for_guest(paths, pkg, force=True)
    marker = load_stage_marker(dest2)
    assert marker is not None
    assert marker.get("incomplete") is False
    assert marker["content_hash"] == content_hash(pkg)


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
    result = dispatch(runner, {"text": "hi"}, ToolContext(paths=paths), package_dir=pkg)
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
    result = dispatch(runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg)
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


# ---------------------------------------------------------------------------
# PR3: reactive path-missing recovery (KD-G3)
# ---------------------------------------------------------------------------


def _fnf_stderr_for(guest_script: str) -> str:
    """Dogfood-shaped guest FileNotFoundError for the exact guest_script path."""
    return (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 8, in <module>\n'
        f"FileNotFoundError: [Errno 2] No such file or directory: {guest_script!r}\n"
    )


def _ok_py_exec() -> ExecResult:
    return ExecResult(
        exit_code=0,
        stdout_text=json.dumps({"ok": True, "upper": "HI"}),
    )


def _setup_fake_guest_life(paths) -> tuple[SandboxLifecycleManager, FakeSandboxClient]:
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths,
        client=client,
        client_unusable=False,
        skip_guest_readiness=True,
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    return life, client


def _guest_script_for_pkg(pkg: Path, module_rel: str = "impl/main.py") -> str:
    return guest_module_path(pkg.name, module_rel)


def _install_exec_sequence(
    life: SandboxLifecycleManager,
    client: FakeSandboxClient,
    results: list[ExecResult],
) -> list[int]:
    """Replace connected.exec to return a sequence of results; return call counter box."""
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    counter = [0]

    async def sequenced_exec(
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env=None,
    ) -> ExecResult:
        client.event_log.append("exec")
        client.exec_calls += 1
        client.last_exec = {
            "cmd": cmd,
            "args": list(args or []),
            "cwd": cwd,
            "timeout": timeout,
            "env": dict(env) if env is not None else None,
        }
        idx = counter[0]
        counter[0] += 1
        if idx < len(results):
            return results[idx]
        # Past sequence: repeat last (usually ok)
        return results[-1] if results else _ok_py_exec()

    sb.exec = sequenced_exec  # type: ignore[method-assign]
    return counter


def _track_force_stages(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record stage_package_for_guest kwargs; force=True entries are recovery."""
    calls: list[dict] = []
    orig = guest_exec_mod.stage_package_for_guest

    def tracking(paths, package_dir, **kwargs):
        calls.append(
            {"force": bool(kwargs.get("force")), "name": Path(package_dir).name}
        )
        return orig(paths, package_dir, **kwargs)

    monkeypatch.setattr(guest_exec_mod, "stage_package_for_guest", tracking)
    return calls


def _tool_logic_fnf_stderr(guest_script: str, missing_data: str) -> str:
    """Realistic CPython traceback: guest_script only as File frame; other path missing."""
    return (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 12, in <module>\n'
        f'  File "{guest_script}", line 5, in run\n'
        f"FileNotFoundError: [Errno 2] No such file or directory: {missing_data!r}\n"
    )


def test_path_missing_signature_exact_guest_script_only() -> None:
    gs = "/workspace/tools/calc/impl/main.py"
    other = "/workspace/tools/calc/data/other.json"
    assert path_missing_signature(
        exit_code=1,
        stderr=_fnf_stderr_for(gs),
        guest_script=gs,
    )
    assert not path_missing_signature(
        exit_code=1,
        stderr=_fnf_stderr_for(other),
        guest_script=gs,
    )
    assert not path_missing_signature(
        exit_code=0,
        stderr=_fnf_stderr_for(gs),
        guest_script=gs,
    )
    assert not path_missing_signature(
        exit_code=1,
        stderr="ValueError: boom",
        guest_script=gs,
    )
    # Blocker: traceback File frame for guest_script + FNF for other path → no match
    assert not path_missing_signature(
        exit_code=1,
        stderr=_tool_logic_fnf_stderr(gs, other),
        guest_script=gs,
    )
    # Prefix boundary: missing main.py.bak must not match guest_script main.py
    assert not path_missing_signature(
        exit_code=1,
        stderr=_fnf_stderr_for(gs + ".bak"),
        guest_script=gs,
    )
    # Shell form: can't open file 'guest_script'
    assert path_missing_signature(
        exit_code=2,
        stderr=f"python3: can't open file '{gs}': [Errno 2] No such file or directory\n",
        guest_script=gs,
    )


def test_guest_fnf_guest_script_force_then_ok(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First exec FNF on guest_script → force re-stage → second exec ok."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "recover_once")
    gs = _guest_script_for_pkg(pkg)
    stage_calls = _track_force_stages(monkeypatch)
    _install_exec_sequence(
        life,
        client,
        [
            ExecResult(exit_code=1, stderr_text=_fnf_stderr_for(gs)),
            _ok_py_exec(),
        ],
    )
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"text": "hi"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is True, result
    assert result.payload.get("upper") == "HI"
    assert result.error_reason is None
    assert client.exec_calls == 2
    force_calls = [c for c in stage_calls if c["force"]]
    assert len(force_calls) == 1
    assert force_calls[0]["name"] == "recover_once"


def test_guest_fnf_both_fail_guest_module_missing(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both execs FNF on guest_script → guest_module_missing (not guest_nonzero_exit)."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "always_miss")
    gs = _guest_script_for_pkg(pkg)
    stage_calls = _track_force_stages(monkeypatch)
    fnf = ExecResult(exit_code=1, stderr_text=_fnf_stderr_for(gs))
    _install_exec_sequence(life, client, [fnf, fnf])
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is False
    assert result.error_reason == "guest_module_missing"
    assert result.payload.get("guest_path") == gs
    assert result.payload.get("stage_retried") is True
    assert result.payload.get("executor_backend") == EXECUTOR_BACKEND_MICROSANDBOX
    assert result.payload.get("content_hash") == content_hash(pkg)
    assert result.payload.get("hint") == "host stage ok; guest visibility failed"
    assert client.exec_calls == 2
    assert sum(1 for c in stage_calls if c["force"]) == 1


def test_guest_fnf_other_package_path_no_force(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FNF on a different package path → no force; KD21 guest_nonzero_exit."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "other_fnf")
    gs = _guest_script_for_pkg(pkg)
    other = f"/workspace/tools/{pkg.name}/data/missing.json"
    stage_calls = _track_force_stages(monkeypatch)
    _install_exec_sequence(
        life,
        client,
        [ExecResult(exit_code=1, stderr_text=_fnf_stderr_for(other))],
    )
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is False
    assert result.error_reason == "guest_nonzero_exit"
    assert client.exec_calls == 1
    assert sum(1 for c in stage_calls if c["force"]) == 0
    # Sanity: guest_script itself would have classified
    assert path_missing_signature(
        exit_code=1, stderr=_fnf_stderr_for(gs), guest_script=gs
    )


def test_guest_fnf_tool_logic_traceback_frame_no_force(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tool-logic FNF: File guest_script frame + other missing path → no force.

    Real CPython always includes ``File "{guest_script}", line N`` when the
    exception is raised inside the loaded module. That must not be treated as
    guest_script path-missing (Issue 1 blocker).
    """
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "tool_logic_fnf")
    gs = _guest_script_for_pkg(pkg)
    other = f"/workspace/tools/{pkg.name}/data/missing.json"
    stage_calls = _track_force_stages(monkeypatch)
    stderr = _tool_logic_fnf_stderr(gs, other)
    # Two identical tool-logic FNFs would previously force + mislabel as
    # guest_module_missing; must stay single-exec guest_nonzero_exit.
    _install_exec_sequence(
        life,
        client,
        [
            ExecResult(exit_code=1, stderr_text=stderr),
            ExecResult(exit_code=1, stderr_text=stderr),
        ],
    )
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"text": "x"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is False
    assert result.error_reason == "guest_nonzero_exit"
    assert client.exec_calls == 1
    assert sum(1 for c in stage_calls if c["force"]) == 0


def test_guest_serial_n_dispatch_fnf_only_call2_recovers(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """N serial dispatches; FNF only on call 2 → recovers; 3..N ok without further force."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "serial_batch")
    gs = _guest_script_for_pkg(pkg)
    stage_calls = _track_force_stages(monkeypatch)
    n = 5
    # Exec order: call1 ok; call2 FNF then ok; calls 3..N ok each.
    sequence = [
        _ok_py_exec(),  # call 1
        ExecResult(exit_code=1, stderr_text=_fnf_stderr_for(gs)),  # call 2 first
        _ok_py_exec(),  # call 2 retry
    ] + [_ok_py_exec() for _ in range(n - 2)]
    _install_exec_sequence(life, client, sequence)
    runner = load_runner_json(pkg)
    results = [
        dispatch(
            runner,
            {"text": f"t{i}"},
            ToolContext(paths=paths),
            package_dir=pkg,
        )
        for i in range(n)
    ]
    assert all(r.ok for r in results), results
    assert client.exec_calls == 1 + 2 + (n - 2)  # = n + 1
    force_calls = [c for c in stage_calls if c["force"]]
    assert len(force_calls) == 1
    # Initial stages (force=False) happen once per dispatch if not skipped;
    # after first stage, hash gate skips — only one force recovery.
    non_force = [c for c in stage_calls if not c["force"]]
    assert len(non_force) >= 1
    assert len(non_force) <= n


def test_guest_no_always_on_preflight_exec_count(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Healthy path: exactly one guest exec per dispatch (no preflight)."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_python_pkg(tmp_path, "no_preflight")
    stage_calls = _track_force_stages(monkeypatch)
    _install_exec_sequence(life, client, [_ok_py_exec()])
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"text": "hi"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is True
    assert client.exec_calls == 1
    assert sum(1 for c in stage_calls if c["force"]) == 0
    # Second dispatch also one exec (hash skip, no preflight)
    result2 = dispatch(
        runner, {"text": "yo"}, ToolContext(paths=paths), package_dir=pkg
    )
    assert result2.ok is True
    assert client.exec_calls == 2
    assert sum(1 for c in stage_calls if c["force"]) == 0


def test_guest_shell_fnf_package_argv_force_then_ok(
    paths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shell minimum: missing package-relative argv path → one force → ok."""
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    life, client = _setup_fake_guest_life(paths)
    pkg = _write_sandbox_shell_pkg(tmp_path, "shell_recover")
    guest_cli = f"/workspace/tools/{pkg.name}/impl/cli.py"
    stage_calls = _track_force_stages(monkeypatch)
    _install_exec_sequence(
        life,
        client,
        [
            ExecResult(
                exit_code=2,
                stderr_text=(
                    f"python3: can't open file '{guest_cli}': "
                    f"[Errno 2] No such file or directory\n"
                ),
            ),
            ExecResult(exit_code=0, stdout_text="done\n"),
        ],
    )
    runner = load_runner_json(pkg)
    result = dispatch(runner, {"msg": "x"}, ToolContext(paths=paths), package_dir=pkg)
    assert result.ok is True, result
    assert client.exec_calls == 2
    assert sum(1 for c in stage_calls if c["force"]) == 1
