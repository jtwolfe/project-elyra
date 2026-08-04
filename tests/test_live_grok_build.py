"""Optional live_grok dogfood skeletons for grok_build (PR6).

Operator-only. **Skipped by default** unless ``ELYRA_LIVE_GROK=1``.

CI / hermetic suite::

    pytest -m 'not llm and not live_grok'

Enable skeletons (still no heavy Grok spend by default)::

    ELYRA_LIVE_GROK=1 pytest tests/test_live_grok_build.py -q

Default tests only exercise **discover** (``find_grok_binary`` / bundled seed)
and pure **validate** dry-runs. Full mode spawns (prompt/design/… ) stay
**operator checklist** work — see ``docs/grok-build-dogfood.md``.
**No** dedicated smoke script; checklist only.

Dogfood matrix D1–D13 (normative design table):
  docs/design/grok-build/design-grok-build-tool.md § "Dogfood matrix / acceptance"
Functionalization (auth seed + zombie/finalize, Grok 0.2.118):
  docs/design/grok-build/design-grok-build-functionalization.md

  D1  mode=prompt “summarize README” in repo
      → ok summary; no token leakage in result.json
      → **required green** on advanced feature/grok-build-tool tip
        before any PR8 discussion (host-absolute cwd)
  D2  Missing OAuth  (preferred with D1)
      → auth_unavailable; task can block honestly
  D3  mode=design small fixture (async)  **gate before PR8** (operator later)
      → job_id → poll completed or needs_human; artifacts/design.md;
        presence worker not blocked for 90m
  D4  mode=implement effort=1 tiny change (async)
      → job completes; tests green; branch not main/working tip hijack
  D5  mode=review --local (async)
      → artifacts/review.md; honest findings
  D6  mode=execute_plan mini design (1–2 PRs), plain-git  **gate before PR8**
      (operator later after D1)
      → PE preflight working; meta argv has --no-graphite;
        stack base working (or documented residual); presence free during run
  D7  mode=deep_research  **experimental only** until PR0a signs
      → only after PR0a: strategy (1)/(2) green; or honest mode_experimental
        if not enabled. See docs/design/grok-build/grok-build-headless-spike.md (PR0a) and
        design KD16 — do not claim completed from background-ack alone.
  D8  Usage  (preferred with D1)
      → headless-shaped usage recorded via adapter; hard-stop prevents launch
  D9  Skill routing
      → self-improve M → implement without execute_plan; async poll steps
  D10 Guest / secret_env law
      → no OAuth in secret_env; guest paths clean
  D11 Skill seed  (preferred with D1)
      → isolated GROK_HOME resolves design+implement (discover gate)
  D12 Mid-run auth
      → multi-hour or forced GROK_AUTH_EXPIRED gets fresh access (mock/live);
        ExternalBinary seed + live provider refresh (no refresh_token seed)
  D13 Reaper restart  (preferred with D1)
      → kill PE mid-job → on restart job interrupted, tokens shredded;
        auth-death must not stay running (zombie reap + honest finalize)

Phase 1 callable on feature tip; full matrix: D1–D6 + D8–D13 green (D7 per spike).
H-spine / PR8 requires **D3 + D6** (after D1 green on advanced tip).
"""

from __future__ import annotations

import os

import pytest

from elyra.instrument.discover import (
    GrokNotFoundError,
    GrokSkillsUnavailableError,
    find_grok_binary,
    find_real_bundled,
)
from elyra.instrument.modes import DEEP_RESEARCH_EXPERIMENTAL
from elyra.instrument.validate import (
    ERROR_MODE_EXPERIMENTAL,
    ERROR_MISSING_PROMPT,
    validate_grok_build_args,
)

# Opt-in: ELYRA_LIVE_GROK=1 (also accept true/yes for operator convenience).
_LIVE = os.environ.get("ELYRA_LIVE_GROK", "").strip().casefold() in {
    "1",
    "true",
    "yes",
}

pytestmark = [
    pytest.mark.live_grok,
    pytest.mark.skipif(
        not _LIVE,
        reason="live_grok skipped (set ELYRA_LIVE_GROK=1 for operator dogfood)",
    ),
]


def test_live_discover_find_grok_binary() -> None:
    """Skeleton: host grok binary discoverable (dogfood prerequisite / D11).

    Does not spawn ``grok`` or spend tokens. Soft-skips when the binary is
    absent so enabling the env alone never hard-fails a bare workstation.
    """
    try:
        path = find_grok_binary()
    except GrokNotFoundError as exc:
        pytest.skip(f"grok binary not on host: {exc}")
    assert path.is_file()
    assert os.access(path, os.X_OK)


def test_live_discover_find_real_bundled() -> None:
    """Skeleton: real install bundled/ with design+implement (D11 skill seed).

    No subprocess. Soft-skip when install layout is incomplete.
    """
    try:
        bundled = find_real_bundled()
    except (GrokNotFoundError, GrokSkillsUnavailableError) as exc:
        pytest.skip(f"real Grok bundled/ not available: {exc}")
    assert bundled.is_dir()
    skills = bundled / "skills"
    assert (skills / "design").exists()
    assert (skills / "implement").exists()


def test_live_validate_dry_run_prompt_and_modes() -> None:
    """Skeleton: pure validate dry-run only — no grok spawn, no spend.

    Confirms mode-conditional table accepts a minimal prompt spawn and rejects
    empty prompt. Heavier D1+ live tool runs stay operator checklist items.
    """
    assert (
        validate_grok_build_args({"mode": "prompt", "prompt": "summarize README"})
        is None
    )
    assert validate_grok_build_args({"mode": "prompt"}) == ERROR_MISSING_PROMPT
    # Long modes validate as spawn-ok without jobs_ready flag (flag optional).
    assert (
        validate_grok_build_args(
            {"mode": "design", "prompt": "small fixture for D3"}
        )
        is None
    )
    assert (
        validate_grok_build_args(
            {
                "mode": "implement",
                "prompt": "tiny change effort=1 for D4",
                "effort": 1,
            }
        )
        is None
    )


def test_live_validate_dry_run_deep_research_experimental() -> None:
    """Skeleton: D7 dry-run — deep_research stays experimental until spike sign-off.

    When DEEP_RESEARCH_EXPERIMENTAL is True (ship default / strategy 3), validate
    returns mode_experimental. No live /deep-research spawn here (spend + spike).
    """
    if not DEEP_RESEARCH_EXPERIMENTAL:
        pytest.skip(
            "deep_research no longer experimental — run signed D7 per spike doc"
        )
    reason = validate_grok_build_args(
        {"mode": "deep_research", "prompt": "trivial query for D7 gate only"},
        deep_research_enabled=False,
    )
    assert reason == ERROR_MODE_EXPERIMENTAL


# ---------------------------------------------------------------------------
# Live heavy-path placeholders (not implemented — operator runs via tool / CLI)
# Uncomment / extend only when deliberately spending SuperGrok / Grok Build.
#
# def test_live_d1_prompt_summarize_readme(): ...  # D1
# def test_live_d2_missing_oauth(): ...            # D2
# def test_live_d3_design_async(): ...             # D3 — before PR8
# def test_live_d4_implement_effort1(): ...        # D4
# def test_live_d5_review_local(): ...             # D5
# def test_live_d6_execute_plan_plain_git(): ...   # D6 — before PR8
# def test_live_d7_deep_research(): ...            # D7 — per spike doc
# def test_live_d8_usage_bridge(): ...             # D8
# def test_live_d9_skill_routing(): ...            # D9
# def test_live_d10_secret_env_law(): ...          # D10 (mostly hermetic unit)
# def test_live_d11_skill_seed_isolated_home(): ...  # D11
# def test_live_d12_mid_run_auth(): ...            # D12
# def test_live_d13_reaper_restart(): ...          # D13
# ---------------------------------------------------------------------------
