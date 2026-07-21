"""Outer context meal assembly for do-loop model calls.

Scope: thin system + sliding glass history + orient near the end.
In scope: token estimate, history strip (no reasoning), budget drop, wake dedupe.
Out of scope: in-turn chain budget, tool messages, do-loop orchestration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.prompts.loader import load_prompt
from elyra.settings import LoopSettings, Settings, default_settings

# Placeholders left empty when not provided (goals/skills land in later PRs).
_EMPTY_PLACEHOLDER = ""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ``len(text) // 4`` (design Stretch 1)."""
    if not text:
        return 0
    return len(text) // 4


def estimate_messages_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    """Sum content token estimates for a message list (roles ignored)."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content is not None:
            total += estimate_tokens(str(content))
    return total


def format_now(now: datetime | None = None) -> str:
    """Human clock frame for orient NOW (local + UTC + weekday)."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    utc = now.astimezone(UTC)
    local = now.astimezone()
    local_label = local.strftime("%Y-%m-%d %H:%M %Z").strip()
    weekday = local.strftime("%A")
    utc_label = utc.strftime("%Y-%m-%d %H:%M UTC")
    return f"{local_label} · {weekday} · {utc_label}"


def fill_orient(
    template: str,
    *,
    now: str,
    self_digest: str = "",
    user_digest: str = "",
    why_now: str = "",
    goals: str = "",
    skill_catalog: str = "",
    skill_bias: str = "",
) -> str:
    """Fill ``prompts/orient.md`` placeholders (simple ``{{NAME}}`` replace)."""
    return (
        template.replace("{{NOW}}", now)
        .replace("{{SELF}}", self_digest if self_digest else _EMPTY_PLACEHOLDER)
        .replace("{{USER}}", user_digest if user_digest else _EMPTY_PLACEHOLDER)
        .replace("{{WHY_NOW}}", why_now if why_now else _EMPTY_PLACEHOLDER)
        .replace("{{GOALS}}", goals if goals else _EMPTY_PLACEHOLDER)
        .replace(
            "{{SKILL_CATALOG}}",
            skill_catalog if skill_catalog else _EMPTY_PLACEHOLDER,
        )
        .replace("{{SKILL_BIAS}}", skill_bias if skill_bias else _EMPTY_PLACEHOLDER)
    )


def _loop_settings(settings: Settings | LoopSettings | None) -> LoopSettings:
    if settings is None:
        return default_settings().loop
    if isinstance(settings, LoopSettings):
        return settings
    return settings.loop


def _glass_to_history(
    glass_history: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep user/assistant speak rows only; strip reasoning and empty content."""
    out: list[dict[str, Any]] = []
    for row in glass_history:
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        content = row.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        if not content:
            continue
        msg: dict[str, Any] = {"role": role, "content": content}
        # Carry id for wake dedupe when present; never include reasoning.
        mid = row.get("id")
        if mid is not None:
            msg["id"] = mid
        out.append(msg)
    return out


def _history_contains_wake(
    history: Sequence[Mapping[str, Any]],
    *,
    wake_content: str | None,
    wake_message_id: str | None,
) -> bool:
    if wake_message_id:
        for msg in history:
            if msg.get("id") == wake_message_id:
                return True
    if wake_content is None:
        return False
    # Same guard as scaffold worker: last user row already is the trigger.
    for msg in reversed(history):
        if msg.get("role") == "user":
            return (msg.get("content") or "") == wake_content
    return False


def _drop_oldest_history(
    history: list[dict[str, Any]],
    *,
    protected_ids: set[str],
    protected_contents: set[str],
) -> bool:
    """Drop the oldest unprotected history message (prefer full pairs).

    Returns True if something was dropped.
    """
    if not history:
        return False

    def is_protected(msg: Mapping[str, Any]) -> bool:
        mid = msg.get("id")
        if mid is not None and mid in protected_ids:
            return True
        content = msg.get("content") or ""
        if content in protected_contents and msg.get("role") == "user":
            return True
        return False

    # Prefer dropping an oldest unprotected user + following assistant as a pair.
    i = 0
    while i < len(history):
        if is_protected(history[i]):
            i += 1
            continue
        # Drop this message; if user followed by unprotected assistant, drop both.
        if (
            history[i].get("role") == "user"
            and i + 1 < len(history)
            and history[i + 1].get("role") == "assistant"
            and not is_protected(history[i + 1])
        ):
            del history[i : i + 2]
            return True
        del history[i]
        return True
    return False


def assemble_outer_meal(
    *,
    glass_history: Sequence[Mapping[str, Any]] | None = None,
    settings: Settings | LoopSettings | None = None,
    paths: ElyraPaths | None = None,
    now: datetime | None = None,
    self_digest: str = "",
    user_digest: str = "",
    why_now: str = "",
    goals: str = "",
    skill_catalog: str = "",
    skill_bias: str = "",
    wake_content: str | None = None,
    wake_message_id: str | None = None,
    system_text: str | None = None,
    orient_template: str | None = None,
    sliding_input_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Build outer prefix messages: system → sliding history → orient.

    Meal order (freeze / design):
    1. Thin system (``prompts/system.md``)
    2. Sliding recent glass history (user + assistant only; **no reasoning**)
    3. Orient near the end (``prompts/orient.md`` filled)

    Budget: ``settings.loop.sliding_input_tokens`` (default 24000). Drops oldest
    history first. Never drops system or orient. Never drops the single
    triggering user text when it is the only copy (protected by content/id).

    Dedupe: if ``wake_content`` / ``wake_message_id`` already appears in glass
    history, do not inject a second copy.
    """
    loop = _loop_settings(settings)
    budget = (
        sliding_input_tokens
        if sliding_input_tokens is not None
        else loop.sliding_input_tokens
    )

    if system_text is None:
        system_text = load_prompt("system", paths=paths)
    if orient_template is None:
        orient_template = load_prompt("orient", paths=paths)

    history = _glass_to_history(glass_history or [])

    # Ensure wake trigger is present once (API often already appended to glass).
    if wake_content and not _history_contains_wake(
        history, wake_content=wake_content, wake_message_id=wake_message_id
    ):
        entry: dict[str, Any] = {"role": "user", "content": wake_content}
        if wake_message_id is not None:
            entry["id"] = wake_message_id
        history.append(entry)

    now_str = format_now(now)
    orient_body = fill_orient(
        orient_template,
        now=now_str,
        self_digest=self_digest,
        user_digest=user_digest,
        why_now=why_now,
        goals=goals,
        skill_catalog=skill_catalog,
        skill_bias=skill_bias,
    )
    orient_msg: dict[str, Any] = {"role": "user", "content": orient_body}

    system_msg: dict[str, Any] = {"role": "system", "content": system_text}

    protected_ids: set[str] = set()
    protected_contents: set[str] = set()
    if wake_message_id:
        protected_ids.add(wake_message_id)
    if wake_content:
        # Protect only when this is the sole copy of the trigger in history.
        copies = sum(
            1
            for m in history
            if m.get("role") == "user" and (m.get("content") or "") == wake_content
        )
        if copies <= 1:
            protected_contents.add(wake_content)

    fixed_tokens = estimate_tokens(system_text) + estimate_tokens(orient_body)

    # Drop oldest history until under budget (or nothing left to drop).
    while history:
        hist_tokens = estimate_messages_tokens(history)
        if fixed_tokens + hist_tokens <= budget:
            break
        if not _drop_oldest_history(
            history,
            protected_ids=protected_ids,
            protected_contents=protected_contents,
        ):
            # Only protected rows remain; stop dropping.
            break

    # Wire format: strip internal ids before returning.
    clean_history = [{"role": m["role"], "content": m["content"]} for m in history]
    return [system_msg, *clean_history, orient_msg]
