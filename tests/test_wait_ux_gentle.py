"""#89 / KD-W1 + KD-W2: wait bar optimistic hide + gentle multi-choice nudge.

Hermetic needles + pure waitArmedForSessionUser cases. No host force-wait.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "elyra" / "runtime" / "web" / "app.js"
TALK_SKILL = REPO_ROOT / "skills" / "bundled" / "talk" / "SKILL.md"
WAIT_TOOL = REPO_ROOT / "tools" / "bundled" / "wait_user" / "TOOL.md"


# ── Pure helper: waitArmedForSessionUser ─────────────────────────────────


def _extract_wait_armed_fn(js: str) -> str:
    # Brace-balanced extract so nested matches_session branch is included (KD24).
    start = js.find("function waitArmedForSessionUser")
    assert start >= 0, "waitArmedForSessionUser must be defined in app.js"
    brace = js.find("{", start)
    assert brace >= 0
    depth = 0
    i = brace
    while i < len(js):
        ch = js[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces in waitArmedForSessionUser")


def test_wait_armed_for_session_user_pure():
    """pending + status pending + matching user_id → armed; matches_session preferred."""
    js = APP_JS.read_text(encoding="utf-8")
    fn_src = _extract_wait_armed_fn(js)

    cases = [
        # (pending, userId, want)
        (None, "operator", False),
        ({}, "operator", False),
        ({"status": "answered", "user_id": "operator"}, "operator", False),
        ({"status": "pending", "user_id": "other"}, "operator", False),
        ({"status": "pending", "user_id": "operator"}, "operator", True),
        ({"status": "pending", "user_id": "jim"}, "jim", True),
        # mismatched types coerced via String()
        ({"status": "pending", "user_id": 1}, "1", True),
        ({"status": "pending"}, "operator", False),  # empty user_id ≠ operator
        ({"status": "pending", "user_id": ""}, "", True),
        # KD24: server matches_session wins over user_id mismatch / match
        (
            {"status": "pending", "user_id": "other", "matches_session": True},
            "operator",
            True,
        ),
        (
            {"status": "pending", "user_id": "operator", "matches_session": False},
            "operator",
            False,
        ),
        (
            {"status": "answered", "user_id": "operator", "matches_session": True},
            "operator",
            False,
        ),
    ]

    node = shutil.which("node")
    if node:
        harness = (
            fn_src
            + "\n"
            + "const cases = "
            + json.dumps([[c[0], c[1], c[2]] for c in cases])
            + ";\n"
            + "for (const [pending, userId, want] of cases) {\n"
            + "  const got = waitArmedForSessionUser(pending, userId);\n"
            + "  if (got !== want) {\n"
            + "    console.error(JSON.stringify({pending, userId, got, want}));\n"
            + "    process.exit(1);\n"
            + "  }\n"
            + "}\n"
            + "console.log('ok');\n"
        )
        proc = subprocess.run(
            [node, "-e", harness],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return

    # Python mirror when node unavailable (same contract as JS / KD24).
    def wait_armed(pending, user_id) -> bool:
        if pending and isinstance(pending.get("matches_session"), bool):
            return pending.get("status") == "pending" and pending["matches_session"]
        if not pending:
            return False
        if pending.get("status") != "pending":
            return False
        return str(pending.get("user_id") or "") == str(user_id or "")

    for pending, user_id, want in cases:
        assert wait_armed(pending, user_id) is want, (pending, user_id, want)


# ── app.js needles: hide after success, refreshStatus both paths ─────────


def test_app_js_wait_bar_optimistic_hide_wiring():
    """KD-W2: hideWaitBarOptimistic only after success; both paths refreshStatus."""
    js = APP_JS.read_text(encoding="utf-8")

    assert "function hideWaitBarOptimistic(" in js
    assert "function waitArmedForSessionUser(" in js
    assert "lastStatusPendingWait" in js

    # Optimistic hide must not clear server-confirmed bookkeeping.
    hide_m = re.search(
        r"function hideWaitBarOptimistic\s*\(\s*\)\s*\{(.*?)\n\}",
        js,
        re.DOTALL,
    )
    assert hide_m is not None
    hide_body = hide_m.group(1)
    assert "lastStatusPendingWait" not in hide_body
    assert "waitBar.hidden = true" in hide_body
    assert "lastPendingWaitId = null" in hide_body

    # refreshStatus captures lastStatusPendingWait then renderWaitBar.
    assert "lastStatusPendingWait = s.pending_wait || null" in js

    # sendWaitChoice: hide after successful fetch, then both refreshes.
    sw_m = re.search(
        r"async function sendWaitChoice\s*\([^)]*\)\s*\{(.*?)\n\}",
        js,
        re.DOTALL,
    )
    assert sw_m is not None
    sw_body = sw_m.group(1)
    assert "hideWaitBarOptimistic()" in sw_body
    # hide must appear after fetchJson (not before POST).
    assert sw_body.index("fetchJson") < sw_body.index("hideWaitBarOptimistic()")
    assert "refreshMessages()" in sw_body
    assert "refreshStatus()" in sw_body
    # Error path restores buttons; no hide in catch.
    catch_m = re.search(r"catch\s*\([^)]*\)\s*\{(.*?)\}", sw_body, re.DOTALL)
    assert catch_m is not None
    assert "hideWaitBarOptimistic" not in catch_m.group(1)
    assert "setWaitChoicesDisabled(false)" in catch_m.group(1)

    # Form submit: waitWasArmed gate + refreshStatus after success.
    assert "waitWasArmed" in js
    assert "waitArmedForSessionUser(lastStatusPendingWait" in js
    # Composer success refreshes status (phase + wait re-arm).
    form_idx = js.index('form.addEventListener("submit"')
    form_slice = js[form_idx : form_idx + 2500]
    assert "hideWaitBarOptimistic()" in form_slice
    assert "refreshStatus()" in form_slice
    assert "refreshMessages({ force: true })" in form_slice or "refreshMessages({force: true})" in form_slice
    # Error path in form must not hide.
    form_catch = re.search(r"catch\s*\([^)]*\)\s*\{(.*?)\}", form_slice, re.DOTALL)
    assert form_catch is not None
    assert "hideWaitBarOptimistic" not in form_catch.group(1)


# ── Gentle skill / TOOL nudge (KD-W1) ────────────────────────────────────


def test_talk_skill_gentle_wait_fork_nudge():
    """Talk skill prefers wait_user for numbered forks — no ALL-CAPS MUST thrash."""
    assert TALK_SKILL.is_file()
    body = TALK_SKILL.read_text(encoding="utf-8")
    lower = body.lower()
    assert "wait_user" in body
    assert "numbered" in lower or "lettered" in lower
    assert "prefer" in lower
    # Prefer-language, not host force-wait thrash.
    assert "MUST arm wait_user" not in body
    assert "MUST WAIT" not in body
    assert "you must always wait_user" not in lower
    # Process step ties forks → choices after speak.
    assert "choices" in lower
    assert "speak" in lower


def test_wait_user_tool_dogfood_fork_example():
    """wait_user TOOL.md carries research-close + numbered forks example."""
    assert WAIT_TOOL.is_file()
    body = WAIT_TOOL.read_text(encoding="utf-8")
    assert "dig Wikipedia lineage" in body
    assert "compare Grokipedia claims" in body
    assert "formal math path" in body
    assert "stop / something else" in body
    assert "Which fork next?" in body
    assert "timeout_seconds" in body
    # Soft prefer, not thrash.
    assert "MUST" not in body or "MUST" not in body.split("## Example")[0]
    # No invent choices.
    assert "invent" in body.lower()

