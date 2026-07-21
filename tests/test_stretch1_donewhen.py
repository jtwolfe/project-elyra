"""Stretch 1 done-when regression pack (PR15).

Maps freeze Done-when claims → covering test modules / runtime symbols.
Does not re-implement full behavioural suites; those live in the named files.

create-tool / create-skill fail-closed requires PR13 gates
(``tests/test_create_tool_gates.py`` + ``elyra/tools/{verify,promote}.py`` /
``builtin/growth.py``). Gates are not deferred "hardening."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.llm.constants import (
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_SLIDING_INPUT_TOKENS,
)
from elyra.settings import default_settings
from elyra.skills.policy import resolve_bundled_skills_root
from elyra.tools.policy import resolve_bundled_tools_root

REPO = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Done-when → primary test modules (docs/stretch-1.md)
# ---------------------------------------------------------------------------

DONE_WHEN_TEST_MAP: dict[str, tuple[str, ...]] = {
    "Presence + wake queue + single worker do-loops": (
        "test_presence_worker.py",
        "test_wake_queue.py",
        "test_doloop.py",
    ),
    "Moments/beats persist; restart-safe": ("test_moment_store.py",),
    "Base tools + sandbox; speak with transport feedback": (
        "test_tools_fs.py",
        "test_sandbox.py",
        "test_speak.py",
        "test_tool_registry.py",
    ),
    "Wait + multi-choice + timeout path": (
        "test_tools_social_wait.py",
        "test_timers.py",
    ),
    "Skills loadable mid-loop; base skills present": (
        "test_skills_catalog.py",
        "test_doloop.py",
    ),
    "Goals/tasks + review-before-close bias": (
        "test_goals.py",
        "test_tools_ledger.py",
    ),
    # PR13 — required for this checkbox; not "hardening later"
    "create-tool / create-skill fail-closed": ("test_create_tool_gates.py",),
    "llama.cpp Gemma path works; context policy documented": (
        "test_config.py",
        "test_loop_context.py",
        "test_doloop.py",
        "test_llm_client_tools.py",
    ),
    "Interjections mid-moment": (
        "test_interject.py",
        "test_api_routing.py",
    ),
}

BASE_TOOLS = (
    "read_file",
    "list_dir",
    "grep",
    "search_replace",
    "run",
    "update_task",
    "update_goal",
    "speak",
    "schedule_wake",
    "wait_user",
    "load_skill",
    "install_tool_draft",
    "verify_tool",
    "promote_tool",
    "install_skill",
)

BASE_SKILLS = (
    "talk",
    "plan-work",
    "do-work",
    "review-work",
    "rest",
    "create-skill",
    "create-tool",
)

CREATE_TOOL_GATE_TESTS = (
    "test_path_jail_rejects_dotdot",
    "test_drafts_never_callable_before_promote",
    "test_hash_invalidate_on_rewrite",
    "test_promote_without_verify_fails",
    "test_builtin_kind_rejected_at_verify_and_promote",
    "test_promote_force_rejected",
    "test_reload_callable_after_promote",
    "test_promote_refuses_overwrite_bundled",
    "test_install_skill_writes_local_only",
)


# ---------------------------------------------------------------------------
# Mapping integrity
# ---------------------------------------------------------------------------


def test_donewhen_map_covers_all_freeze_claims() -> None:
    """Every freeze Done-when line has at least one covering test module."""
    assert len(DONE_WHEN_TEST_MAP) == 9
    for claim, modules in DONE_WHEN_TEST_MAP.items():
        assert modules, f"empty map for claim: {claim}"
        for name in modules:
            path = TESTS / name
            assert path.is_file(), f"missing test module for {claim!r}: {name}"


def test_create_tool_checkbox_requires_pr13_gates_file() -> None:
    """create-tool done-when is satisfied by PR13 suite, not a future hardening dump."""
    modules = DONE_WHEN_TEST_MAP["create-tool / create-skill fail-closed"]
    assert modules == ("test_create_tool_gates.py",)
    text = (TESTS / "test_create_tool_gates.py").read_text(encoding="utf-8")
    for fn in CREATE_TOOL_GATE_TESTS:
        assert f"def {fn}" in text, f"PR13 gate test missing: {fn}"


# ---------------------------------------------------------------------------
# Runtime symbols / no one-shot path
# ---------------------------------------------------------------------------


def test_no_oneshot_worker_module() -> None:
    """PR12c: one-shot chat worker must stay deleted."""
    assert not (REPO / "elyra" / "loop" / "worker.py").exists()
    # Presence owns the worker; do-loop is multi-hop only.
    from elyra.presence.worker import PresenceWorker
    from elyra.loop.doloop import run_do_loop

    assert callable(run_do_loop)
    assert PresenceWorker is not None


def test_presence_and_doloop_imports() -> None:
    from elyra.moment import MomentStore
    from elyra.presence import TimerService, WakeQueue
    from elyra.presence.worker import PresenceWorker
    from elyra.loop.doloop import run_do_loop

    assert MomentStore and WakeQueue and TimerService and PresenceWorker
    assert callable(run_do_loop)


def test_create_tool_runtime_gates_importable() -> None:
    """PR13 runtime enforcement (fail-closed even if skill prose is ignored)."""
    from elyra.tools.builtin.growth import (
        install_skill,
        install_tool_draft,
        promote_tool,
        verify_tool,
    )
    from elyra.tools.promote import promote_draft_tool
    from elyra.tools.verify import content_hash, load_verify_record, verify_stage_dir

    assert callable(install_tool_draft)
    assert callable(verify_tool)
    assert callable(promote_tool)
    assert callable(install_skill)
    assert callable(promote_draft_tool)
    assert callable(content_hash)
    assert callable(load_verify_record)
    assert callable(verify_stage_dir)


# ---------------------------------------------------------------------------
# Base catalog on disk
# ---------------------------------------------------------------------------


def test_base_tools_packages_on_disk() -> None:
    root = resolve_bundled_tools_root()
    assert root.is_dir()
    for name in BASE_TOOLS:
        package = root / name
        assert (package / "TOOL.md").is_file(), f"missing tool package: {name}"


def test_base_skills_packages_on_disk() -> None:
    root = resolve_bundled_skills_root()
    assert root.is_dir()
    for name in BASE_SKILLS:
        package = root / name
        assert (package / "SKILL.md").is_file(), f"missing skill package: {name}"


def test_create_tool_skill_mentions_verify_promote() -> None:
    """Skill checklist must match fail-closed lifecycle (dogfood with runtime)."""
    root = resolve_bundled_skills_root()
    body = (root / "create-tool" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("draft", "verify", "promote"):
        assert needle in body.lower(), f"create-tool skill missing {needle!r}"


# ---------------------------------------------------------------------------
# Context policy: -c ceiling vs sliding 24k
# ---------------------------------------------------------------------------


def test_context_ceiling_vs_sliding_defaults() -> None:
    """Inference law: sliding meal well under KV ceiling."""
    assert CONTEXT_WINDOW_TOKENS == 86_000
    assert DEFAULT_SLIDING_INPUT_TOKENS == 24_000
    assert DEFAULT_SLIDING_INPUT_TOKENS < CONTEXT_WINDOW_TOKENS
    s = default_settings()
    assert s.loop.sliding_input_tokens == 24_000
    assert s.loop.in_turn_max_tokens == 24_000
    assert s.loop.sliding_input_tokens < CONTEXT_WINDOW_TOKENS


def test_inference_docs_document_ceiling_vs_sliding() -> None:
    text = (REPO / "docs" / "inference.md").read_text(encoding="utf-8")
    assert "86000" in text or "86_000" in text or "86 000" in text or "-c" in text
    assert "24000" in text or "24k" in text or "24_000" in text
    assert "ceiling" in text.lower()
    assert "sliding" in text.lower()


def test_stretch1_donewhen_checked_in_docs() -> None:
    stretch = (REPO / "docs" / "stretch-1.md").read_text(encoding="utf-8")
    # All nine freeze boxes checked
    assert stretch.count("- [x]") >= 9
    assert "- [ ]" not in stretch.split("## Done when")[-1].split("Not required")[0]
    # create-tool explicitly ties to PR13
    done = stretch.split("## Done when")[-1]
    assert "PR13" in done
    assert "create-tool" in done.lower() or "create-tool" in stretch.lower()


def test_readme_documents_llm_marker_and_donewhen() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "pytest -m 'not llm'" in readme or 'pytest -m "not llm"' in readme
    assert "pytest -m llm" in readme
    assert "PR13" in readme
    assert "test_create_tool_gates" in readme
    assert "Presence" in readme and "do-loop" in readme.lower()


# ---------------------------------------------------------------------------
# LLM marker still registered (real Gemma path documented / skippable)
# ---------------------------------------------------------------------------


def test_llm_marker_registered() -> None:
    """Real-model tests use @pytest.mark.llm; suite must declare the marker."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "llm:" in text
    assert "Gemma" in text or "llama" in text.lower() or "model" in text.lower()
    doloop = (TESTS / "test_doloop.py").read_text(encoding="utf-8")
    client = (TESTS / "test_llm_client_tools.py").read_text(encoding="utf-8")
    assert "@pytest.mark.llm" in doloop
    assert "@pytest.mark.llm" in client
