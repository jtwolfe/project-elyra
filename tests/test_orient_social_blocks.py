"""Orient social map: Participants / Recently active / Active chats (C12 PR5).

Covers fill_orient defaults (old call sites), DM vs group participants,
pure-work empty Participants/Active chats, soft recently-active message-first
vs session secondary, and zero/edge paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.conversations import ConversationsStore
from elyra.identity.orient_blocks import (
    build_active_chats_block,
    build_participants_block,
    build_recently_active_block,
    coerce_orient_int,
)
from elyra.loop.context import assemble_outer_meal, fill_orient
from elyra.prompts.loader import load_prompt
from elyra.runtime.client_sessions import ClientSessionsRegistry
from elyra.users import UsersStore


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def users(paths) -> UsersStore:
    store = UsersStore(paths)
    store.ensure_layout()
    return store


@pytest.fixture
def convs(paths) -> ConversationsStore:
    store = ConversationsStore(paths)
    store.ensure_layout()
    return store


# ---------------------------------------------------------------------------
# fill_orient API churn / template placeholders
# ---------------------------------------------------------------------------


def test_fill_orient_defaults_omit_new_kwargs():
    """Old call sites without participants/recently_active/active_chats stay green."""
    template = (
        "U={{USER}}\nP={{PARTICIPANTS}}\nR={{RECENTLY_ACTIVE}}\n"
        "A={{ACTIVE_CHATS}}\nW={{WHY_NOW}}\n"
    )
    text = fill_orient(template, now="n", user_digest="them", why_now="wake")
    assert "U=them" in text
    assert "W=wake" in text
    # Empty defaults leave blank (same as SELF/USER empty path).
    assert "P=\n" in text or text.split("P=")[1].startswith("\n")
    assert "R=\n" in text or text.split("R=")[1].startswith("\n")
    assert "A=\n" in text or text.split("A=")[1].startswith("\n")
    assert "{{" not in text


def test_fill_orient_new_placeholders_filled():
    template = (
        "## Participants\n{{PARTICIPANTS}}\n"
        "## Recently active users\n{{RECENTLY_ACTIVE}}\n"
        "## Active chats\n{{ACTIVE_CHATS}}\n"
    )
    text = fill_orient(
        template,
        now="n",
        participants="- Jim (jim) — peer DM",
        recently_active="- Jim (jim) · last glass ~1h ago",
        active_chats="- dm:jim (dm) · Jim",
    )
    assert "- Jim (jim) — peer DM" in text
    assert "last glass" in text
    assert "dm:jim" in text
    assert "{{PARTICIPANTS}}" not in text
    assert "{{RECENTLY_ACTIVE}}" not in text
    assert "{{ACTIVE_CHATS}}" not in text


def test_ship_orient_template_has_social_placeholders(paths):
    body = load_prompt("orient", paths=paths)
    assert "{{PARTICIPANTS}}" in body
    assert "{{RECENTLY_ACTIVE}}" in body
    assert "{{ACTIVE_CHATS}}" in body
    assert "## Participants" in body
    assert "## Recently active users" in body
    assert "## Active chats" in body
    # USER remains a single work-origin slot (not multi-user dump).
    assert "## USER" in body
    assert body.index("## USER") < body.index("## Participants")


def test_assemble_outer_meal_defaults_without_social_kwargs():
    """assemble_outer_meal old kwargs still produce a meal; placeholders empty."""
    meal = assemble_outer_meal(
        glass_history=[{"role": "user", "content": "hi", "id": "m1"}],
        system_text="# sys\n",
        orient_template=(
            "# Orient\n## USER\n{{USER}}\n## Participants\n{{PARTICIPANTS}}\n"
            "## Active chats\n{{ACTIVE_CHATS}}\n## Why now\n{{WHY_NOW}}\n"
            "## SELF\n{{SELF}}\n## NOW\n{{NOW}}\n## Goals / tasks\n{{GOALS}}\n"
            "## Skills available\n{{SKILL_CATALOG}}\n## Soft skill bias\n{{SKILL_BIAS}}\n"
            "## Recently active users\n{{RECENTLY_ACTIVE}}\n"
        ),
        user_digest="Operator",
        why_now="user message",
        sliding_input_tokens=50_000,
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    orient = meal[-1]["content"]
    assert "Operator" in orient
    assert "{{PARTICIPANTS}}" not in orient
    assert "{{ACTIVE_CHATS}}" not in orient
    assert "{{RECENTLY_ACTIVE}}" not in orient


# ---------------------------------------------------------------------------
# Participants: pure-work empty / DM / group
# ---------------------------------------------------------------------------


def test_participants_pure_work_empty(users, convs):
    convs.ensure_dm("jim")
    text = build_participants_block(
        social=False,
        conversation_id="dm:jim",
        peer_user_id="jim",
        conversations=convs,
        users=users,
    )
    assert text == ""


def test_participants_social_dm(users, convs):
    users.create_user("Jim", user_id="jim", provisional=False)
    convs.ensure_dm("jim")
    text = build_participants_block(
        social=True,
        conversation_id="dm:jim",
        peer_user_id="jim",
        conversations=convs,
        users=users,
    )
    assert "jim" in text
    assert "Jim" in text
    assert "peer DM" in text
    assert text.startswith("- ")


def test_participants_social_dm_without_store_uses_peer(users):
    users.create_user("Sam", user_id="sam", provisional=True)
    text = build_participants_block(
        social=True,
        conversation_id=None,
        peer_user_id="sam",
        conversations=None,
        users=users,
    )
    assert "sam" in text
    assert "peer DM" in text


def test_participants_group_lists_members(users, convs):
    users.create_user("Jim", user_id="jim", provisional=False)
    users.create_user("Sam", user_id="sam", provisional=True)
    group = convs.create_group(name="Dinner", members=["jim", "sam", "operator"])
    text = build_participants_block(
        social=True,
        conversation_id=group["id"],
        conversations=convs,
        users=users,
    )
    assert "jim" in text
    assert "sam" in text
    assert "operator" in text
    assert "provisional guest" in text
    # Not multi-USER dump: still bullets, not full profiles as USER.
    assert "## USER" not in text


def test_participants_social_missing_conv_and_peer_empty(users, convs):
    text = build_participants_block(
        social=True,
        conversation_id="group:does-not-exist",
        peer_user_id=None,
        conversations=convs,
        users=users,
    )
    assert text == ""


def test_participants_missing_group_with_peer_not_dm_fallback(users, convs):
    """group: missing store must not mislabel speaker as peer DM (review #2)."""
    users.create_user("Jim", user_id="jim", provisional=False)
    text = build_participants_block(
        social=True,
        conversation_id="group:does-not-exist",
        peer_user_id="jim",
        conversations=convs,
        users=users,
    )
    assert text == ""
    assert "peer DM" not in text


def test_participants_token_budget_drops_trailing(users, convs):
    members = []
    for i in range(12):
        uid = f"u{i:02d}"
        users.create_user(f"User{i}", user_id=uid, provisional=True)
        members.append(uid)
    group = convs.create_group(name="Crowd", members=members)
    text = build_participants_block(
        social=True,
        conversation_id=group["id"],
        conversations=convs,
        users=users,
        max_tokens=30,
    )
    # Under budget and non-empty (some members fit).
    assert text
    from elyra.loop.context import estimate_tokens

    assert estimate_tokens(text) <= 30


# ---------------------------------------------------------------------------
# Active chats: pure-work empty / social list / zero-state
# ---------------------------------------------------------------------------


def test_active_chats_pure_work_empty(users, convs):
    convs.ensure_dm("jim")
    text = build_active_chats_block(
        social=False, conversations=convs, users=users, limit=6
    )
    assert text == ""


def test_active_chats_zero_conversations(users, convs):
    text = build_active_chats_block(
        social=True, conversations=convs, users=users, limit=6
    )
    assert text == ""


def test_active_chats_social_lists_with_cap(users, convs):
    users.create_user("Jim", user_id="jim", provisional=False)
    users.create_user("Sam", user_id="sam", provisional=True)
    convs.ensure_dm("jim")
    convs.ensure_dm("sam")
    convs.create_group(name="Project", members=["jim", "sam"])
    text = build_active_chats_block(
        social=True, conversations=convs, users=users, limit=2
    )
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert "dm:" in text or "group:" in text


def test_active_chats_none_store_empty(users):
    assert (
        build_active_chats_block(
            social=True, conversations=None, users=users, limit=6
        )
        == ""
    )


def test_active_chats_limit_zero_empty(users, convs):
    convs.ensure_dm("operator")
    assert (
        build_active_chats_block(
            social=True, conversations=convs, users=users, limit=0
        )
        == ""
    )


# ---------------------------------------------------------------------------
# Soft recently-active: messages first, session secondary, edges
# ---------------------------------------------------------------------------


def test_recently_active_empty_when_no_activity(users):
    text = build_recently_active_block(
        glass_rows=[],
        session_entries=[],
        users=users,
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    assert text == ""


def test_recently_active_messages_primary(users):
    users.create_user("Jim", user_id="jim", provisional=False)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows = [
        {
            "role": "user",
            "user_id": "jim",
            "content": "hi",
            "created_at": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "role": "assistant",
            "user_id": "jim",
            "content": "hello",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    text = build_recently_active_block(
        glass_rows=rows,
        session_entries=None,
        users=users,
        hours=24,
        limit=8,
        now=now,
    )
    assert "jim" in text
    assert "last glass" in text
    assert "online" not in text.lower()


def test_recently_active_ignores_old_messages(users):
    users.create_user("Jim", user_id="jim", provisional=False)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows = [
        {
            "role": "user",
            "user_id": "jim",
            "content": "old",
            "created_at": (now - timedelta(hours=48)).isoformat(),
        }
    ]
    text = build_recently_active_block(
        glass_rows=rows, users=users, hours=24, now=now
    )
    assert text == ""


def test_recently_active_session_secondary_only_with_activity_at(users, paths):
    users.create_user("Jim", user_id="jim", provisional=False)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    # Session without activity_at (GET mint style) must not count.
    ghost = [
        {
            "client_id": "c1",
            "user_id": "jim",
            "updated_at": now.isoformat(),
            # no activity_at
        }
    ]
    text = build_recently_active_block(
        glass_rows=[],
        session_entries=ghost,
        users=users,
        hours=24,
        now=now,
    )
    assert text == ""

    # Mutating put stamps activity_at → secondary fill.
    reg = ClientSessionsRegistry(paths)
    reg.put("client-jim", user_id="jim")
    entries = reg.list_entries()
    assert any(e.get("activity_at") for e in entries)
    text2 = build_recently_active_block(
        glass_rows=[],
        session_entries=entries,
        users=users,
        hours=24,
        now=datetime.now(UTC),
    )
    assert "jim" in text2
    assert "session touch" in text2


def test_recently_active_session_unknown_user_skipped(users):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    entries = [
        {
            "client_id": "c-x",
            "user_id": "not_a_real_user_xyz",
            "activity_at": now.isoformat(),
        }
    ]
    text = build_recently_active_block(
        glass_rows=[],
        session_entries=entries,
        users=users,
        hours=24,
        now=now,
    )
    assert text == ""


def test_recently_active_message_wins_over_session(users):
    users.create_user("Jim", user_id="jim", provisional=False)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows = [
        {
            "role": "user",
            "user_id": "jim",
            "content": "hi",
            "created_at": (now - timedelta(minutes=30)).isoformat(),
        }
    ]
    entries = [
        {
            "client_id": "c1",
            "user_id": "jim",
            "activity_at": (now - timedelta(minutes=5)).isoformat(),
        }
    ]
    text = build_recently_active_block(
        glass_rows=rows,
        session_entries=entries,
        users=users,
        hours=24,
        now=now,
    )
    assert "last glass" in text
    assert "session touch" not in text


def test_recently_active_limit_and_hours_nonpositive(users):
    users.create_user("Jim", user_id="jim", provisional=False)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows = [
        {
            "role": "user",
            "user_id": "jim",
            "created_at": now.isoformat(),
            "content": "x",
        }
    ]
    assert (
        build_recently_active_block(
            glass_rows=rows, users=users, hours=0, now=now
        )
        == ""
    )
    assert (
        build_recently_active_block(
            glass_rows=rows, users=users, limit=0, now=now
        )
        == ""
    )


def test_coerce_orient_int_honors_zero():
    """Operator 0 must disable blocks (review #3); not coerced to default."""
    assert coerce_orient_int(0, 800) == 0
    assert coerce_orient_int(0, 24) == 0
    assert coerce_orient_int(None, 24) == 24
    assert coerce_orient_int(8, 24) == 8
    assert coerce_orient_int("6", 24) == 6
    assert coerce_orient_int("bad", 24) == 24


def test_recently_active_includes_speaker_beyond_meal_tail(users):
    """Primary RA uses full within-T history, not last-80 meal rows (review #1).

    Hermetic: speaker with only an early-within-T message still appears when
    the full glass list is passed (worker uses list_messages(limit=0)).
    """
    users.create_user("Early", user_id="early", provisional=True)
    users.create_user("Late", user_id="late", provisional=True)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows: list[dict] = []
    # 90 filler assistant+user pairs after early speaker (would push early
    # out of a last-80 meal tail if we only scanned that).
    rows.append(
        {
            "role": "user",
            "user_id": "early",
            "content": "first",
            "created_at": (now - timedelta(hours=3)).isoformat(),
        }
    )
    for i in range(90):
        rows.append(
            {
                "role": "assistant",
                "user_id": "late",
                "content": f"a{i}",
                "created_at": (now - timedelta(hours=2, minutes=i)).isoformat(),
            }
        )
        rows.append(
            {
                "role": "user",
                "user_id": "late",
                "content": f"u{i}",
                "created_at": (now - timedelta(hours=1, minutes=i % 50)).isoformat(),
            }
        )
    # Meal-tail simulation: last 80 rows would drop "early".
    meal_tail = rows[-80:]
    assert not any(r.get("user_id") == "early" for r in meal_tail)

    full_text = build_recently_active_block(
        glass_rows=rows, users=users, hours=24, limit=8, now=now
    )
    assert "early" in full_text
    assert "last glass" in full_text

    tail_text = build_recently_active_block(
        glass_rows=meal_tail, users=users, hours=24, limit=8, now=now
    )
    assert "early" not in tail_text


def test_recently_active_dedupes_and_caps(users):
    users.create_user("A", user_id="a", provisional=True)
    users.create_user("B", user_id="b", provisional=True)
    users.create_user("C", user_id="c", provisional=True)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    rows = [
        {
            "role": "user",
            "user_id": "a",
            "created_at": (now - timedelta(hours=3)).isoformat(),
            "content": "1",
        },
        {
            "role": "user",
            "user_id": "a",
            "created_at": (now - timedelta(hours=1)).isoformat(),
            "content": "2",
        },
        {
            "role": "user",
            "user_id": "b",
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "content": "3",
        },
        {
            "role": "user",
            "user_id": "c",
            "created_at": (now - timedelta(minutes=10)).isoformat(),
            "content": "4",
        },
    ]
    text = build_recently_active_block(
        glass_rows=rows, users=users, hours=24, limit=2, now=now
    )
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    # Newest first: c then a (or b) — a has more recent than b.
    assert "c" in lines[0]
    assert text.count("(a)") <= 1


# ---------------------------------------------------------------------------
# Preserve single USER work-origin (integration with fill_orient)
# ---------------------------------------------------------------------------


def test_social_blocks_do_not_replace_user_slot(users, convs):
    users.create_user("Jim", user_id="jim", provisional=False)
    convs.ensure_dm("jim")
    participants = build_participants_block(
        social=True,
        conversation_id="dm:jim",
        conversations=convs,
        users=users,
    )
    template = "## USER\n{{USER}}\n## Participants\n{{PARTICIPANTS}}\n"
    text = fill_orient(
        template,
        now="n",
        user_digest="# Jim\npeer profile body",
        participants=participants,
    )
    assert "# Jim\npeer profile body" in text
    assert "peer DM" in text
    # USER section still has the work-origin digest alone before Participants.
    user_part = text.split("## Participants")[0]
    assert "peer profile body" in user_part
    assert "peer DM" not in user_part
