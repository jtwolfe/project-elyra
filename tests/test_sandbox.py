"""Tests for persistent sandbox jail and run."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.sandbox import (
    OUTPUT_CAP_BYTES,
    PathEscapeError,
    Sandbox,
    resolve,
)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return Sandbox(paths)


# --- path jail ---


def test_resolve_happy_relative(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "a.txt").write_text("ok\n", encoding="utf-8")
    got = resolve(root, "a.txt")
    assert got == (root / "a.txt").resolve()
    assert got.read_text(encoding="utf-8") == "ok\n"


def test_path_dotdot_escape_denied(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n", encoding="utf-8")
    with pytest.raises(PathEscapeError, match="escapes"):
        resolve(root, "../secret.txt")
    with pytest.raises(PathEscapeError, match="escapes"):
        resolve(root, "sub/../../secret.txt")


def test_absolute_path_escape_denied(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    with pytest.raises(PathEscapeError, match="escapes"):
        resolve(root, str(outside))


def test_absolute_path_inside_allowed(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    inside = root / "in.txt"
    inside.write_text("yes\n", encoding="utf-8")
    got = resolve(root, str(inside.resolve()))
    assert got == inside.resolve()


def test_symlink_escape_denied(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("LEAK\n", encoding="utf-8")
    link = root / "escape.link"
    link.symlink_to(outside)
    with pytest.raises(PathEscapeError, match="escapes"):
        resolve(root, "escape.link")


def test_symlink_inside_allowed(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("inside\n", encoding="utf-8")
    link = root / "alias.txt"
    link.symlink_to(target)
    got = resolve(root, "alias.txt")
    assert got.read_text(encoding="utf-8") == "inside\n"


def test_sandbox_resolve_uses_data_sandbox(sandbox: Sandbox) -> None:
    assert sandbox.root.name == "sandbox"
    p = sandbox.write_text("notes/hi.txt", "hello\n")
    assert p.is_file()
    assert sandbox.read_text("notes/hi.txt") == "hello\n"


# --- FS happy path ---


def test_list_read_write_happy(sandbox: Sandbox) -> None:
    sandbox.write_text("foo.txt", "one\n")
    sandbox.write_text("dir/bar.txt", "two\n")
    names = sandbox.list_dir(".")
    assert "foo.txt" in names
    assert "dir" in names
    assert sandbox.list_dir("dir") == ["bar.txt"]
    assert sandbox.read_text("foo.txt") == "one\n"
    assert sandbox.read_text("dir/bar.txt") == "two\n"


def test_write_dotdot_denied(sandbox: Sandbox) -> None:
    with pytest.raises(PathEscapeError):
        sandbox.write_text("../escape.txt", "x")


def test_read_symlink_escape_denied(sandbox: Sandbox) -> None:
    outside = sandbox.root.parent / "host_secret.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    link = sandbox.root / "leak"
    link.symlink_to(outside)
    with pytest.raises(PathEscapeError):
        sandbox.read_text("leak")


def test_grep_simple(sandbox: Sandbox) -> None:
    sandbox.write_text("a.txt", "alpha\nbeta\n")
    sandbox.write_text("b/c.txt", "alphabet\n")
    hits = sandbox.grep("alpha")
    paths = {h["path"] for h in hits}
    assert "a.txt" in paths
    assert "b/c.txt" in paths
    assert all("alpha" in str(h["text"]) for h in hits)


def test_grep_regex(sandbox: Sandbox) -> None:
    sandbox.write_text("n.txt", "n1\nn22\n")
    hits = sandbox.grep(r"n\d{2}", regex=True)
    assert len(hits) == 1
    assert hits[0]["line"] == 2


def test_search_replace(sandbox: Sandbox) -> None:
    sandbox.write_text("edit.txt", "foo bar foo\n")
    n = sandbox.search_replace("edit.txt", "foo", "baz")
    assert n == 2
    assert sandbox.read_text("edit.txt") == "baz bar baz\n"
    n2 = sandbox.search_replace("edit.txt", "baz", "x", count=1)
    assert n2 == 1
    assert sandbox.read_text("edit.txt") == "x bar baz\n"


def test_search_replace_empty_old_rejected(sandbox: Sandbox) -> None:
    sandbox.write_text("e.txt", "x")
    with pytest.raises(ValueError, match="non-empty"):
        sandbox.search_replace("e.txt", "", "y")


# --- run ---


def test_run_argv_list(sandbox: Sandbox) -> None:
    result = sandbox.run([sys.executable, "-c", "print('hi')"])
    assert result.returncode == 0
    assert result.timed_out is False
    assert "hi" in result.stdout
    assert result.argv[0] == sys.executable


def test_run_string_uses_shlex_not_shell(sandbox: Sandbox) -> None:
    """shell=False: ';' must not inject a second command."""
    marker = sandbox.root / "injected.flag"
    # With shell=True this would create the flag; with shlex + shell=False,
    # touch is only an argument to echo (or fails as a single exec path).
    cmd = f"echo safe; touch {marker.name}"
    result = sandbox.run(cmd)
    assert not marker.exists(), "shell injection must not run second command"
    # echo receives extra args; should not have failed as shell metachar exec
    assert result.timed_out is False
    assert "safe" in result.stdout or result.returncode in (0, 1, 127)


def test_run_never_shell_true_metachar(sandbox: Sandbox) -> None:
    """``rm`` after ``;`` must not execute."""
    victim = sandbox.root / "keep_me.txt"
    victim.write_text("stay\n", encoding="utf-8")
    result = sandbox.run("echo hi; rm keep_me.txt")
    assert victim.is_file()
    assert victim.read_text(encoding="utf-8") == "stay\n"
    assert result.argv[0] == "echo"


def test_run_timeout(sandbox: Sandbox) -> None:
    result = sandbox.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.3,
    )
    assert result.timed_out is True
    assert result.returncode != 0


def test_run_stdout_cap(sandbox: Sandbox) -> None:
    # Produce more than OUTPUT_CAP_BYTES of stdout.
    n = OUTPUT_CAP_BYTES + 50_000
    result = sandbox.run(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {n})"],
        timeout=30,
        output_cap=OUTPUT_CAP_BYTES,
    )
    assert result.returncode == 0
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= OUTPUT_CAP_BYTES


def test_run_stream_cap_retains_only_cap_bytes(sandbox: Sandbox) -> None:
    """Chunked collection: retained stdout length equals cap, not full producer."""
    cap = 4096
    # Multi-chunk write well above cap (not a single tiny buffer).
    n = 2_000_000
    result = sandbox.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write('y' * {n}); sys.stdout.flush()",
        ],
        timeout=30,
        output_cap=cap,
    )
    assert result.stdout_truncated is True
    retained = result.stdout.encode("utf-8")
    assert len(retained) == cap
    assert retained == b"y" * cap


def test_run_negative_output_cap_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(ValueError, match="output_cap"):
        sandbox.run([sys.executable, "-c", "pass"], output_cap=-1)


def test_run_timeout_returns_promptly(sandbox: Sandbox) -> None:
    """Post-timeout path must not hang (bounded drain after kill)."""
    import time

    t0 = time.monotonic()
    result = sandbox.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        timeout=0.2,
    )
    elapsed = time.monotonic() - t0
    assert result.timed_out is True
    # grace + join slack; must be far below the 60s child sleep
    assert elapsed < 8.0


def test_run_env_blocks_ld_preload(sandbox: Sandbox) -> None:
    result = sandbox.run(
        [
            sys.executable,
            "-c",
            "import os; print(repr(os.environ.get('LD_PRELOAD')))",
        ],
        env={"LD_PRELOAD": "/tmp/evil.so", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert "None" in result.stdout


def test_run_env_blocks_pythonpath_and_home_override(sandbox: Sandbox) -> None:
    result = sandbox.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('PYTHONPATH', '')); "
            "print(os.environ.get('HOME', ''))",
        ],
        env={
            "PYTHONPATH": "/tmp/evil_py",
            "HOME": "/tmp",
            "BASH_ENV": "/tmp/evil",
        },
    )
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == ""
    assert lines[1] == str(sandbox.root)


def test_empty_path_rejected(sandbox: Sandbox, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        resolve(tmp_path, "")
    with pytest.raises(ValueError, match="non-empty"):
        sandbox.write_text("", "x")
    with pytest.raises(ValueError, match="non-empty"):
        sandbox.write_text("   ", "x")


def test_run_cwd_is_sandbox_root(sandbox: Sandbox) -> None:
    sandbox.write_text("here.txt", "yes\n")
    result = sandbox.run(
        [sys.executable, "-c", "print(open('here.txt').read())"],
    )
    assert result.returncode == 0
    assert "yes" in result.stdout


def test_run_env_minimal_no_host_secrets(sandbox: Sandbox, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ELYRA_SECRET", "topsecret")
    result = sandbox.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('OPENAI_API_KEY', '')); "
            "print(os.environ.get('ELYRA_SECRET', '')); "
            "print(os.environ.get('HOME', ''))",
        ],
    )
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == ""
    assert lines[1] == ""
    assert lines[2] == str(sandbox.root)


def test_run_empty_command_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(ValueError, match="empty"):
        sandbox.run("")
    with pytest.raises(ValueError, match="empty"):
        sandbox.run([])


def test_sandbox_persistent_not_auto_cleared(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    s1 = Sandbox(paths)
    s1.write_text("persist.txt", "keep\n")
    s2 = Sandbox(paths)
    assert s2.read_text("persist.txt") == "keep\n"
    # ensure_data_dirs again must not clear sandbox
    paths.ensure_data_dirs()
    assert s2.read_text("persist.txt") == "keep\n"
