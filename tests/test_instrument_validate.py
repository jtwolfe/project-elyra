"""Unit tests: elyra.instrument.validate — mode-conditional table."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.instrument.validate import (
    ERROR_BASE_BRANCH_MISSING,
    ERROR_DESIGN_DOC_MISSING,
    ERROR_INVALID_ARGS,
    ERROR_INVALID_EFFORT,
    ERROR_MISSING_DESIGN_DOC_PATH,
    ERROR_MISSING_PROMPT,
    ERROR_MISSING_REPO,
    ERROR_MODE_EXPERIMENTAL,
    ERROR_MODE_NOT_READY,
    ERROR_USAGE_HARD_STOP,
    is_poll_only,
    validate_grok_build_args,
)


def test_invalid_args_not_mapping() -> None:
    assert validate_grok_build_args(None) == ERROR_INVALID_ARGS
    assert validate_grok_build_args("x") == ERROR_INVALID_ARGS  # type: ignore[arg-type]


def test_missing_mode_spawn() -> None:
    assert validate_grok_build_args({}) == ERROR_INVALID_ARGS
    assert validate_grok_build_args({"mode": "nope"}) == ERROR_INVALID_ARGS


def test_poll_job_id_xor_prefers_poll() -> None:
    # job_id set → poll only; spawn fields ignored
    assert is_poll_only({"job_id": "job-1"}) is True
    assert (
        validate_grok_build_args(
            {"job_id": "job-1", "prompt": "also", "mode": "prompt"}
        )
        is None
    )
    assert validate_grok_build_args({"job_id": "  "}) == ERROR_INVALID_ARGS  # empty → spawn


def test_prompt_requires_prompt() -> None:
    assert validate_grok_build_args({"mode": "prompt"}) == ERROR_MISSING_PROMPT
    assert validate_grok_build_args({"mode": "prompt", "prompt": ""}) == ERROR_MISSING_PROMPT
    assert validate_grok_build_args({"mode": "prompt", "prompt": "  hi "}) is None


def test_design_requires_prompt() -> None:
    assert validate_grok_build_args({"mode": "design"}) == ERROR_MISSING_PROMPT
    assert validate_grok_build_args({"mode": "design", "prompt": "spec"}) is None


def test_implement_prompt_and_effort() -> None:
    assert validate_grok_build_args({"mode": "implement"}) == ERROR_MISSING_PROMPT
    assert (
        validate_grok_build_args({"mode": "implement", "prompt": "x", "effort": 0})
        == ERROR_INVALID_EFFORT
    )
    assert (
        validate_grok_build_args({"mode": "implement", "prompt": "x", "effort": 6})
        == ERROR_INVALID_EFFORT
    )
    assert (
        validate_grok_build_args({"mode": "implement", "prompt": "x", "effort": 3})
        is None
    )
    assert validate_grok_build_args({"mode": "implement", "prompt": "x"}) is None


def test_execute_plan_design_doc_path(tmp_path: Path) -> None:
    assert (
        validate_grok_build_args({"mode": "execute_plan"})
        == ERROR_MISSING_DESIGN_DOC_PATH
    )
    missing = tmp_path / "nope.md"
    assert (
        validate_grok_build_args(
            {"mode": "execute_plan", "design_doc_path": str(missing)}
        )
        == ERROR_DESIGN_DOC_MISSING
    )
    doc = tmp_path / "design.md"
    doc.write_text("# design\n", encoding="utf-8")
    assert (
        validate_grok_build_args(
            {"mode": "execute_plan", "design_doc_path": str(doc)}
        )
        is None
    )
    # explicit override without FS
    assert (
        validate_grok_build_args(
            {"mode": "execute_plan", "design_doc_path": "/virtual/doc.md"},
            design_doc_exists=True,
            check_design_doc_fs=False,
        )
        is None
    )
    assert (
        validate_grok_build_args(
            {"mode": "execute_plan", "design_doc_path": str(doc)},
            base_branch_ok=False,
        )
        == ERROR_BASE_BRANCH_MISSING
    )


def test_deep_research_experimental() -> None:
    assert (
        validate_grok_build_args({"mode": "deep_research", "prompt": "q"})
        == ERROR_MODE_EXPERIMENTAL
    )
    assert (
        validate_grok_build_args(
            {"mode": "deep_research", "prompt": "q"},
            deep_research_enabled=True,
        )
        is None
    )
    assert (
        validate_grok_build_args(
            {"mode": "deep_research"},
            deep_research_enabled=True,
        )
        == ERROR_MISSING_PROMPT
    )


def test_review_target_optional() -> None:
    assert validate_grok_build_args({"mode": "review"}) is None
    assert validate_grok_build_args({"mode": "review", "target": "local"}) is None
    assert validate_grok_build_args({"mode": "review", "target": "main"}) is None


def test_long_mode_not_ready() -> None:
    assert (
        validate_grok_build_args(
            {"mode": "design", "prompt": "x"},
            jobs_ready=False,
        )
        == ERROR_MODE_NOT_READY
    )
    # prompt is short — jobs_ready False does not block
    assert (
        validate_grok_build_args(
            {"mode": "prompt", "prompt": "x"},
            jobs_ready=False,
        )
        is None
    )


def test_usage_and_repo_flags() -> None:
    assert (
        validate_grok_build_args(
            {"mode": "prompt", "prompt": "x"},
            usage_allowed=False,
        )
        == ERROR_USAGE_HARD_STOP
    )
    assert (
        validate_grok_build_args(
            {"mode": "prompt", "prompt": "x"},
            repo_resolved=False,
        )
        == ERROR_MISSING_REPO
    )


@pytest.mark.parametrize("effort", [True, 1.5, "2", 0, 9])
def test_invalid_effort_types(effort) -> None:
    assert (
        validate_grok_build_args(
            {"mode": "implement", "prompt": "x", "effort": effort}
        )
        == ERROR_INVALID_EFFORT
    )
