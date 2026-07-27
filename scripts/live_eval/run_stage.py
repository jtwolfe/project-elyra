#!/usr/bin/env python3
"""Live-eval scenarios loader + fail-closed operator entry.

Hermetic tests import ``Scenario`` / ``load_scenarios`` only (import-safe).
Operator ``main()`` always fails closed: the Gemma/llama-server path is removed.
Retarget to xAI dogfood or a future OpenAI-compat eval harness is out of scope.

Usage (always exits 2):
  python scripts/live_eval/run_stage.py --stage 0
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Repo root on sys.path when invoked as scripts/live_eval/run_stage.py
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Scenarios (stdlib YAML-ish load — no PyYAML dependency)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    intent: str
    prompt: str
    expects_tools: bool = True
    expects_speak: bool = True
    expects_no_flood: bool = True
    # Continuous multi-moment (PR9 / design §Eval Plan)
    continuous: bool = False
    preseed_ready_task: bool = False
    notes: str = ""
    expects_no_moment_continue: bool | None = None
    expects_no_task_ready_storm: bool | None = None


@dataclass(frozen=True)
class StageConfig:
    stage: int
    name: str
    temperature: float
    top_p: Any
    top_k: Any
    reasoning_budget_tokens: Any
    max_tool_hops: int
    moment_wall_clock_minutes: int
    poll_timeout_seconds: float
    scenarios: tuple[Scenario, ...]


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s in ("null", "Null", "NULL", "~"):
        return None
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def load_scenarios(path: Path | None = None) -> StageConfig:
    """Load scenarios.yaml with a minimal subset parser (no PyYAML)."""
    path = path or (_HERE / "scenarios.yaml")
    text = path.read_text(encoding="utf-8")
    knobs: dict[str, Any] = {
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 64,
        "reasoning_budget_tokens": None,
    }
    caps: dict[str, Any] = {
        "max_tool_hops": 12,
        "moment_wall_clock_minutes": 10,
        "poll_timeout_seconds": 620,
    }
    stage = 0
    name = "baseline"
    scenarios: list[Scenario] = []
    section: str | None = None
    cur: dict[str, Any] | None = None
    cur_expects: dict[str, Any] = {}

    def _flush() -> None:
        nonlocal cur, cur_expects
        if cur and cur.get("id") and cur.get("prompt") is not None:
            # expects.moment_continue: false → no outer continue after settle
            mc_raw = cur_expects.get("moment_continue")
            expects_no_mc: bool | None = None
            if mc_raw is not None:
                expects_no_mc = not bool(mc_raw)
            storm_raw = cur_expects.get("task_ready_storm")
            expects_no_storm: bool | None = None
            if storm_raw is not None:
                expects_no_storm = not bool(storm_raw)
            scenarios.append(
                Scenario(
                    id=str(cur["id"]),
                    intent=str(cur.get("intent") or ""),
                    prompt=str(cur["prompt"]),
                    expects_tools=bool(cur_expects.get("tools", True)),
                    expects_speak=bool(cur_expects.get("speak", True)),
                    expects_no_flood=not bool(
                        cur_expects.get("flood", False)
                    ),  # flood: false → expect no flood
                    continuous=bool(cur.get("continuous", False)),
                    preseed_ready_task=bool(cur.get("preseed_ready_task", False)),
                    notes=str(cur.get("notes") or ""),
                    expects_no_moment_continue=expects_no_mc,
                    expects_no_task_ready_storm=expects_no_storm,
                )
            )
        cur = None
        cur_expects = {}

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0 and stripped.endswith(":") and not stripped.startswith("-"):
            _flush()
            key = stripped[:-1].strip()
            if key == "scenarios":
                section = "scenarios"
            elif key == "knobs":
                section = "knobs"
            elif key == "eval_caps":
                section = "eval_caps"
            else:
                section = None
            continue

        if indent == 0 and ":" in stripped and not stripped.startswith("-"):
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            if key == "stage" and val:
                stage = int(val)
            elif key == "name" and val:
                name = _parse_scalar(val) if val else name
            continue

        if section in ("knobs", "eval_caps") and ":" in stripped:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            target = knobs if section == "knobs" else caps
            if val:
                target[key] = _parse_scalar(val)
            continue

        if section == "scenarios":
            if stripped.startswith("- "):
                _flush()
                cur = {}
                cur_expects = {}
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, _, v = rest.partition(":")
                    cur[k.strip()] = _parse_scalar(v.strip()) if v.strip() else None
                continue
            if cur is None:
                continue
            if stripped == "expects:":
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k, v = k.strip(), v.strip()
                if k in (
                    "tools",
                    "speak",
                    "flood",
                    "moment_continue",
                    "task_ready_storm",
                ):
                    cur_expects[k] = _parse_scalar(v) if v else None
                else:
                    cur[k] = _parse_scalar(v) if v else ""
    _flush()

    if not scenarios:
        raise SystemExit(f"no scenarios loaded from {path}")

    return StageConfig(
        stage=stage,
        name=str(name),
        temperature=float(knobs.get("temperature", 0.2)),
        top_p=knobs.get("top_p"),
        top_k=knobs.get("top_k"),
        reasoning_budget_tokens=knobs.get("reasoning_budget_tokens"),
        max_tool_hops=int(caps.get("max_tool_hops", 12)),
        moment_wall_clock_minutes=int(caps.get("moment_wall_clock_minutes", 10)),
        poll_timeout_seconds=float(caps.get("poll_timeout_seconds", 620)),
        scenarios=tuple(scenarios),
    )



# ---------------------------------------------------------------------------
# Operator entry (fail-closed)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Minimal parser so ``--help`` works; flags are not executed."""
    p = argparse.ArgumentParser(
        prog="run_stage",
        description=(
            "Live-eval stage runner — FAIL-CLOSED. "
            "Gemma/llama-server path removed; scenario YAML still loads for hermetic tests."
        ),
    )
    p.add_argument("--stage", type=int, default=0, help="Ignored (fail-closed)")
    p.add_argument(
        "--scenarios-file",
        type=Path,
        default=None,
        help="Optional scenarios.yaml path (for load_scenarios / tests only)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    """Operator entry — always fail-closed (exit 2).

    ``--help`` still works via argparse. Scenario loaders remain import-safe
    for hermetic ``tests/test_live_eval_scenarios.py``.
    """
    build_parser().parse_args(argv)
    print(
        "live_eval run_stage: Gemma/llama-server path removed.\n"
        "Use xAI dogfood (`elyra start`) or a future OpenAI-compat eval harness.\n"
        "Scenario YAML still loads for hermetic tests "
        "(tests/test_live_eval_scenarios.py).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
