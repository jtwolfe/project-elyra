"""Hermetic tests for live_eval scenarios.yaml (no GPU / no live run).

CI stays green without llama-server. Live multi-moment continuous runs are
operator-driven via ``scripts/live_eval/run_stage.py`` (see docs/live-eval.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LIVE_EVAL = _ROOT / "scripts" / "live_eval"
if str(_LIVE_EVAL) not in sys.path:
    sys.path.insert(0, str(_LIVE_EVAL))

from run_stage import Scenario, load_scenarios  # noqa: E402

SCENARIOS_PATH = _LIVE_EVAL / "scenarios.yaml"

# Baseline continuous-OFF regression (must remain default-safe)
BASELINE_IDS = ("S-social", "S-tools", "S-mono")

# Continuous ON scenarios (PR9 / design §Eval Plan)
CONT_REQUIRED_IDS = (
    "S-cont-speak-only",
    "S-cont-tools",
    "S-cont-task-ready-prefer",
)


@pytest.fixture(scope="module")
def stage_cfg():
    assert SCENARIOS_PATH.is_file(), f"missing {SCENARIOS_PATH}"
    return load_scenarios(SCENARIOS_PATH)


def test_scenarios_yaml_loads(stage_cfg):
    assert stage_cfg.stage == 5
    assert len(stage_cfg.scenarios) >= 6
    by_id = {s.id: s for s in stage_cfg.scenarios}
    for sid in BASELINE_IDS + CONT_REQUIRED_IDS:
        assert sid in by_id, f"missing scenario {sid}"


def test_baseline_continuous_off(stage_cfg):
    """Continuous OFF preserves S-social / S-tools / S-mono product defaults."""
    by_id = {s.id: s for s in stage_cfg.scenarios}
    for sid in BASELINE_IDS:
        s = by_id[sid]
        assert s.continuous is False, f"{sid} must leave continuous OFF"
        assert s.preseed_ready_task is False
        assert s.expects_tools is True
        assert s.expects_speak is True
        assert s.expects_no_flood is True
        assert s.prompt.strip(), f"{sid} needs a prompt"


def test_cont_speak_only(stage_cfg):
    s = {x.id: x for x in stage_cfg.scenarios}["S-cont-speak-only"]
    assert s.continuous is True
    assert s.expects_speak is True
    assert s.expects_no_moment_continue is True
    assert "hello" in s.prompt.lower() or "speak" in s.prompt.lower()


def test_cont_tools_list_dir_create_goal(stage_cfg):
    s = {x.id: x for x in stage_cfg.scenarios}["S-cont-tools"]
    assert s.continuous is True
    assert s.expects_tools is True
    assert s.expects_speak is True
    pl = s.prompt.lower()
    assert "list_dir" in pl
    assert "create_goal" in pl
    assert "speak" in pl


def test_cont_task_ready_prefer(stage_cfg):
    s = {x.id: x for x in stage_cfg.scenarios}["S-cont-task-ready-prefer"]
    assert s.continuous is True
    assert s.preseed_ready_task is True
    assert s.expects_no_task_ready_storm is True
    assert s.expects_no_moment_continue is True


def test_scenario_dataclass_defaults():
    s = Scenario(id="x", intent="i", prompt="p")
    assert s.continuous is False
    assert s.preseed_ready_task is False
    assert s.expects_no_moment_continue is None
    assert s.expects_no_task_ready_storm is None


def test_ship_knobs_present(stage_cfg):
    assert stage_cfg.temperature == 0.6
    assert stage_cfg.top_p == 0.95
    assert stage_cfg.top_k == 64
    assert stage_cfg.reasoning_budget_tokens == 2048
    assert stage_cfg.poll_timeout_seconds >= 600
