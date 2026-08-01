"""Unit tests: elyra.instrument.argv — slash body, human-gate, effort-vs-CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.instrument.argv import (
    EXECUTE_PLAN_BASE_AND_POLICY,
    HUMAN_GATE_POLICY,
    build_argv_for_mode,
    build_cli_argv,
    build_slash_prompt,
    review_slash_target,
)
from elyra.instrument.modes import Mode


def test_prompt_mode_no_slash() -> None:
    body = build_slash_prompt(Mode.PROMPT, prompt="hello world")
    assert body == "hello world"
    assert not body.startswith("/")


def test_design_slash_and_human_gate_and_artifact_suffix(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    body = build_slash_prompt(
        Mode.DESIGN,
        prompt="Design the widget",
        artifacts_dir=art,
    )
    assert body.startswith("/design Design the widget")
    assert HUMAN_GATE_POLICY in body
    assert "HEADLESS PE POLICY" in body
    assert f"Write the final design document to: {art / 'design.md'}" in body
    assert f"Write a short design summary to: {art / 'summary.md'}" in body


def test_implement_effort_inside_prompt_not_cli() -> None:
    body = build_slash_prompt(
        Mode.IMPLEMENT,
        prompt="fix the bug",
        effort=2,
    )
    assert "/implement --effort 2 fix the bug" in body
    assert HUMAN_GATE_POLICY in body

    argv, pbody = build_argv_for_mode(
        Mode.IMPLEMENT,
        prompt="fix the bug",
        effort=2,
        cwd="/repo",
    )
    # PE effort only in -p body
    assert "--effort 2" in pbody
    assert pbody == body or "--effort 2" in pbody
    # Never as bare CLI flag after binary
    # argv is [grok, -p, body, --output-format, json, --always-approve, --cwd, /repo]
    assert argv[0] == "grok"
    assert "-p" in argv
    # No CLI token exactly '--effort' as a separate argv element
    assert "--effort" not in argv
    assert "--reasoning-effort" not in argv
    # effort digits may appear only inside the -p string value
    p_idx = argv.index("-p")
    assert "--effort 2" in argv[p_idx + 1]
    for i, tok in enumerate(argv):
        if i == p_idx + 1:
            continue
        assert tok != "--effort"
        assert not (tok.isdigit() and int(tok) == 2 and i > 0 and argv[i - 1] == "--effort")


def test_execute_plan_no_graphite_default_and_working_policy(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("x", encoding="utf-8")
    body = build_slash_prompt(
        Mode.EXECUTE_PLAN,
        design_doc_path=str(doc),
        effort=1,
        auto_pr=True,
    )
    assert body.startswith(f"/execute-plan {doc}")
    assert "--no-graphite" in body
    assert "--auto-pr" in body
    assert "--effort 1" in body
    assert "working" in body
    assert EXECUTE_PLAN_BASE_AND_POLICY in body or "Stack bottom base branch" in body
    assert HUMAN_GATE_POLICY in body
    assert "--instructions" in body


def test_execute_plan_use_graphite_omits_no_graphite(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("x", encoding="utf-8")
    body = build_slash_prompt(
        Mode.EXECUTE_PLAN,
        design_doc_path=str(doc),
        use_graphite=True,
    )
    assert "--no-graphite" not in body


def test_deep_research_slash() -> None:
    body = build_slash_prompt(Mode.DEEP_RESEARCH, prompt="history of X")
    assert body.startswith("/deep-research history of X")
    # no human-gate for deep_research per HUMAN_GATE_MODES
    assert HUMAN_GATE_POLICY not in body


@pytest.mark.parametrize(
    "target,expect",
    [
        (None, "/review --local"),
        ("local", "/review --local"),
        ("feature/foo", "/review --branch feature/foo"),
        ("42", "/review --pr 42"),
        ("https://github.com/o/r/pull/7", "/review --pr https://github.com/o/r/pull/7"),
    ],
)
def test_review_slash_target(target, expect) -> None:
    assert review_slash_target(target) == expect
    body = build_slash_prompt(Mode.REVIEW, target=target)
    assert body.startswith(expect)
    assert HUMAN_GATE_POLICY in body


def test_build_cli_argv_flags() -> None:
    argv = build_cli_argv(
        "hello",
        cwd="/tmp/repo",
        always_approve=True,
        model="grok-4",
        max_turns=10,
        output_format="json",
    )
    assert argv[0] == "grok"
    assert argv[argv.index("-p") + 1] == "hello"
    assert "--output-format" in argv
    assert "json" in argv
    assert "--always-approve" in argv
    assert "--cwd" in argv
    assert "/tmp/repo" in argv
    assert "-m" in argv
    assert "grok-4" in argv
    assert "--max-turns" in argv
    assert "--effort" not in argv


def test_build_cli_argv_prompt_file() -> None:
    argv = build_cli_argv("ignored", prompt_file="/tmp/p.txt")
    assert "--prompt-file" in argv
    assert "/tmp/p.txt" in argv
    assert "-p" not in argv


def test_human_gate_modes_coverage() -> None:
    for mode in (Mode.DESIGN, Mode.IMPLEMENT, Mode.EXECUTE_PLAN, Mode.REVIEW):
        kwargs: dict = {}
        if mode is Mode.EXECUTE_PLAN:
            kwargs["design_doc_path"] = "/doc.md"
        else:
            kwargs["prompt"] = "x"
        body = build_slash_prompt(mode, **kwargs)
        assert "HEADLESS PE POLICY" in body, mode
