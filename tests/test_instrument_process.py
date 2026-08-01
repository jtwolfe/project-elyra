"""Unit tests: instrument process broker (PR2) — mocked subprocess."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from elyra.instrument import process as process_mod
from elyra.instrument.process import (
    ProcessResult,
    build_child_env,
    run_grok,
    truncate_capture,
)


def test_process_module_has_no_usage_skills_harvest_imports() -> None:
    """process.py must stay subprocess-only (KD9)."""
    src = Path(process_mod.__file__).read_text(encoding="utf-8")
    forbidden = (
        "elyra.llm.usage",
        "elyra.instrument.usage",
        "elyra.instrument.jobs",
        "elyra.instrument.reaper",
        "elyra.instrument.result",
        "harvest_artifacts",
        "UsageMeter",
        "elyra.skills",
    )
    for needle in forbidden:
        assert needle not in src, f"process.py must not reference {needle!r}"


def test_truncate_capture() -> None:
    assert truncate_capture("abc", 10) == "abc"
    long = "x" * 1000
    out = truncate_capture(long, 100)
    assert len(out) <= 100 + 50  # marker overhead
    assert "truncated" in out
    assert out.startswith("x")


def test_build_child_env_sets_grok_home(tmp_path: Path) -> None:
    home = tmp_path / "gh"
    home.mkdir()
    env = build_child_env(grok_home=home, base={"PATH": "/bin", "KEEP": "1"})
    assert env["GROK_HOME"] == str(home.resolve())
    assert env["KEEP"] == "1"
    assert env["CI"] == "1"
    assert env["GROK_NO_BROWSER"] == "1"
    # No OAuth inject.
    assert "XAI_API_KEY" not in env or env.get("XAI_API_KEY") is None


def test_run_grok_success_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grok_home = tmp_path / "grok_home"
    grok_home.mkdir()
    seen: dict[str, Any] = {}

    class FakeProc:
        def __init__(self, *a: Any, **kw: Any) -> None:
            seen["argv"] = a[0] if a else kw.get("args")
            seen["env"] = kw.get("env")
            seen["cwd"] = kw.get("cwd")
            seen["start_new_session"] = kw.get("start_new_session")
            seen["shell"] = kw.get("shell")
            self.pid = 4242
            self.returncode = 0

        def communicate(
            self, input: Any = None, timeout: float | None = None
        ) -> tuple[str, str]:
            seen["timeout"] = timeout
            return ('{"ok":true,"text":"hi"}', "")

        def kill(self) -> None:
            seen["killed"] = True

    monkeypatch.setattr(process_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(process_mod.os, "getpgid", lambda pid: pid)

    result = run_grok(
        ["grok", "-p", "hello", "--output-format", "json"],
        grok_home=grok_home,
        cwd=tmp_path,
        timeout_s=30,
    )
    assert isinstance(result, ProcessResult)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert '"ok":true' in result.stdout
    assert result.stderr == ""
    assert seen["shell"] is False
    assert seen["start_new_session"] is True
    assert seen["env"]["GROK_HOME"] == str(grok_home.resolve())
    assert seen["timeout"] == 30.0
    assert seen["cwd"] == str(tmp_path)


def test_run_grok_timeout_kills_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grok_home = tmp_path / "gh"
    grok_home.mkdir()
    kills: list[tuple[int, int]] = []
    communicate_calls = {"n": 0}

    class FakeProc:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.pid = 99
            self.returncode = None

        def communicate(
            self, input: Any = None, timeout: float | None = None
        ) -> tuple[str, str]:
            communicate_calls["n"] += 1
            if communicate_calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="grok", timeout=timeout or 1)
            self.returncode = -9
            return ("", "hung auth")

        def kill(self) -> None:
            kills.append((-1, -1))

    def fake_killpg(pgid: int, sig: int) -> None:
        kills.append((pgid, sig))

    monkeypatch.setattr(process_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(process_mod.os, "getpgid", lambda pid: 99)
    monkeypatch.setattr(process_mod.os, "killpg", fake_killpg)
    # skip grace-loop re-check: pretend process gone after SIGTERM
    def killpg_then_gone(pgid: int, sig: int) -> None:
        kills.append((pgid, sig))
        if sig == 0:
            raise ProcessLookupError()

    monkeypatch.setattr(process_mod.os, "killpg", killpg_then_gone)

    result = run_grok(
        ["grok", "-p", "x"],
        grok_home=grok_home,
        timeout_s=1,
    )
    assert result.timed_out is True
    assert result.exit_code != 0
    assert any(sig != 0 for _, sig in kills)
    assert any(pgid == 99 for pgid, _ in kills)


def test_run_grok_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProc:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.pid = 1
            self.returncode = 2

        def communicate(
            self, input: Any = None, timeout: float | None = None
        ) -> tuple[str, str]:
            return ("", "boom")

    monkeypatch.setattr(process_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(process_mod.os, "getpgid", lambda pid: pid)

    result = run_grok(
        ["grok", "-p", "x"],
        grok_home=tmp_path,
        timeout_s=5,
    )
    assert result.exit_code == 2
    assert result.stderr == "boom"
    assert result.timed_out is False


def test_run_grok_rejects_bad_args(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_grok([], grok_home=tmp_path, timeout_s=1)
    with pytest.raises(ValueError):
        run_grok(["grok"], grok_home=tmp_path, timeout_s=0)


def test_run_grok_truncates_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = "Z" * 10_000

    class FakeProc:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.pid = 1
            self.returncode = 0

        def communicate(
            self, input: Any = None, timeout: float | None = None
        ) -> tuple[str, str]:
            return (big, big)

    monkeypatch.setattr(process_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(process_mod.os, "getpgid", lambda pid: pid)

    result = run_grok(
        ["grok"],
        grok_home=tmp_path,
        timeout_s=5,
        capture_max_chars=200,
    )
    assert len(result.stdout) < 10_000
    assert "truncated" in result.stdout
