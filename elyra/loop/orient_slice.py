"""Pure orient-slice formatters: skill catalog, soft bias, goals/tasks.

Scope: turn store/catalog data into short strings for ``prompts/orient.md``.
In scope: name+description catalog lines, wake-kind + ledger-aware soft bias,
  budgeted goals/tasks slice with protected wake ids.
Out of scope: presence orchestration, continuous policy, hard gates,
  bias-aware catalog ranking, multi-line bias essays.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from elyra.loop.context import estimate_tokens

# Soft skill bias strings (exact; not hard gates).
BIAS_TALK = "Prefer skill: talk (social reply first; speak before wait)."
BIAS_DO_WORK = "Prefer skill: do-work (act on the ready task)."
BIAS_TIMER_LINKED = "Prefer skill: do-work or plan-work for the linked work."
BIAS_TIMER_GENERIC = (
    "Prefer skill: do-work or rest depending on the timer reason."
)
BIAS_MOMENT_CONTINUE = (
    "Prefer skill: do-work or plan-work; use create-tool/create-skill only if "
    "capability is the bottleneck. Load rest if nothing honest remains."
)
BIAS_BACKGROUND = "Prefer skill: rest unless orient shows ready work."
BIAS_WAIT_TIMEOUT = (
    "Prefer skill: talk if user owed a follow-up; else do-work/rest from ledger."
)
BIAS_PLAN_WORK = (
    "Prefer skill: plan-work (open goal needs tasks before execution)."
)
BIAS_REST = "Prefer skill: rest (nothing honest open on the ledger)."

_GOAL_STATUSES = frozenset({"open", "review"})
_TASK_STATUSES_PRIMARY = frozenset({"ready", "in_progress", "blocked"})
_TASK_STATUSES_OPTIONAL = frozenset({"pending"})
_TASK_STATUSES_DO_WORK = frozenset({"ready", "in_progress"})
_GOAL_STATUSES_OPEN = frozenset({"open", "review"})

_EMPTY_GOALS = "(no open goals)"

# Soft caps on individual field lengths (chars) so one goal cannot dominate.
_ACCEPTANCE_MAX_CHARS = 200
_NOTES_MAX_CHARS = 120
_TITLE_MAX_CHARS = 120


def format_skill_catalog(
    catalog: Sequence[Mapping[str, Any]] | None,
    *,
    max_tokens: int | None = None,
) -> str:
    """Bullet lines ``- {name}: {description}`` from ``SkillCatalog.catalog()``.

    Catalog is already name + description only and sorted by name. YAGNI: no
    bias-aware drop ranking. When ``max_tokens`` is a positive int and the full
    list is over budget, drop trailing (alphabetically last) skills until under
    cap. ``max_tokens is None`` means uncapped; ``max_tokens <= 0`` yields an
    empty string (mis-set budget must not silently disable the cap).
    """
    if not catalog:
        return ""
    # Explicit non-positive budget → empty (do not treat 0 as unlimited).
    if max_tokens is not None and max_tokens <= 0:
        return ""
    lines: list[str] = []
    for item in catalog:
        name = item.get("name") or ""
        if not name:
            continue
        desc = item.get("description") or ""
        if isinstance(desc, str):
            desc = desc.strip()
        else:
            desc = str(desc).strip()
        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            lines.append(f"- {name}")

    if max_tokens is None:
        return "\n".join(lines)

    # Simple trailing drop only — never bias-aware ranking (later if needed).
    while lines:
        text = "\n".join(lines)
        if estimate_tokens(text) <= max_tokens:
            return text
        lines.pop()
    return ""


def format_skill_bias(
    wake_kind: str,
    payload: Mapping[str, Any] | None = None,
    goals: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Soft one-line skill bias (not a hard gate).

    Preference: social wake-kind always wins; else ledger shape when
    ``goals`` is provided; else existing wake-kind table.

    Parameters
    ----------
    wake_kind:
        Wake kind string (e.g. ``user_message``, ``task_ready``).
    payload:
        Optional wake payload (timer linkage, task_id, …).
    goals:
        Optional goal dicts as returned by ``GoalsStore.list_goals()``
        (nested ``tasks``). ``None`` = ledger-unaware (wake-kind table only).
        Empty sequence = no goals on disk (treated as empty ledger).
    """
    kind = (wake_kind or "").strip()
    pl = payload or {}

    # 1. Social always wins (glass presence first) — ignore ledger.
    if kind in ("user_message", "wait_reply"):
        return BIAS_TALK

    # 2. Ledger-aware path only when caller supplied goals (incl. empty list).
    #    When goals is not None the wake-kind table is NOT consulted.
    if goals is not None:
        has_ready_or_in_progress, has_open_or_review_goal = _summarize_ledger_shape(
            goals
        )
        if has_ready_or_in_progress:
            return BIAS_DO_WORK
        if has_open_or_review_goal:
            return BIAS_PLAN_WORK
        return BIAS_REST

    # 3. goals is None: existing wake-kind table (unit-compat / non-production).
    return _wake_kind_bias_table(kind, pl)


def _summarize_ledger_shape(
    goals: Sequence[Mapping[str, Any]],
) -> tuple[bool, bool]:
    """Scan goal dicts for ready/in_progress tasks and open/review goals.

    Uses mapping key access (match ``format_goals_slice`` / live ``list_goals()``).
    Non-mappings are skipped; non-list ``tasks`` treated as empty.
    """
    has_ready_or_in_progress = False
    has_open_or_review_goal = False
    for g in goals:
        if not isinstance(g, Mapping):
            continue
        if g.get("status") not in _GOAL_STATUSES_OPEN:
            continue
        has_open_or_review_goal = True
        tasks = g.get("tasks")
        if not isinstance(tasks, (list, tuple)):
            tasks = []
        for t in tasks:
            if not isinstance(t, Mapping):
                continue
            if t.get("status") in _TASK_STATUSES_DO_WORK:
                has_ready_or_in_progress = True
    return has_ready_or_in_progress, has_open_or_review_goal


def _wake_kind_bias_table(kind: str, pl: Mapping[str, Any]) -> str:
    """Wake-kind-only bias table used when ``goals is None``."""
    if kind == "task_ready":
        return BIAS_DO_WORK
    if kind == "timer":
        if pl.get("task_id") or pl.get("goal_id"):
            return BIAS_TIMER_LINKED
        return BIAS_TIMER_GENERIC
    if kind == "moment_continue":
        return BIAS_MOMENT_CONTINUE
    if kind == "background":
        return BIAS_BACKGROUND
    if kind == "wait_timeout":
        return BIAS_WAIT_TIMEOUT
    return ""


def format_goals_slice(
    goals: Sequence[Mapping[str, Any]] | None,
    *,
    max_tokens: int = 600,
    protect_goal_ids: Iterable[str] | None = None,
    protect_task_ids: Iterable[str] | None = None,
    include_pending_tasks: bool = False,
) -> str:
    """Open/review goals + ready/in_progress/blocked tasks, token-budgeted.

    Parameters
    ----------
    goals:
        Goal dicts as returned by ``GoalsStore.list_goals()`` (nested tasks).
    max_tokens:
        Soft cap via ``estimate_tokens`` (``len // 4``). Drop oldest-updated
        non-protected goals first; always keep protected goal/task ids from
        the wake payload when present in the ledger.
    include_pending_tasks:
        When True, also list ``pending`` tasks (extra space only — default off
        so the slice stays action-focused).
    """
    if not goals:
        return _EMPTY_GOALS

    protect_g = {str(x) for x in (protect_goal_ids or ()) if x}
    protect_t = {str(x) for x in (protect_task_ids or ()) if x}
    # Goals that own a protected task are also protected.
    for g in goals:
        if not isinstance(g, Mapping):
            continue
        for t in g.get("tasks") or []:
            if isinstance(t, Mapping) and str(t.get("id") or "") in protect_t:
                gid = str(g.get("id") or "")
                if gid:
                    protect_g.add(gid)

    task_ok = set(_TASK_STATUSES_PRIMARY)
    if include_pending_tasks:
        task_ok |= _TASK_STATUSES_OPTIONAL

    candidates: list[dict[str, Any]] = []
    for g in goals:
        if not isinstance(g, Mapping):
            continue
        status = g.get("status")
        if status not in _GOAL_STATUSES:
            continue
        candidates.append(dict(g))

    if not candidates:
        return _EMPTY_GOALS

    def _updated_key(g: Mapping[str, Any]) -> str:
        return str(g.get("updated_at") or g.get("created_at") or "")

    # Newest-first for inclusion preference; drop oldest-updated first.
    candidates.sort(key=_updated_key, reverse=True)

    def _render_one(g: Mapping[str, Any]) -> str:
        gid = g.get("id") or "?"
        title = _truncate(str(g.get("title") or ""), _TITLE_MAX_CHARS)
        status = g.get("status") or "?"
        lines = [f"Goal {gid} [{status}]: {title}"]
        acceptance = g.get("acceptance")
        if acceptance:
            acc = _truncate(str(acceptance), _ACCEPTANCE_MAX_CHARS)
            lines.append(f"  acceptance: {acc}")
        for t in g.get("tasks") or []:
            if not isinstance(t, Mapping):
                continue
            t_status = t.get("status")
            tid = str(t.get("id") or "")
            # Always show protected tasks even if status would filter them out.
            if t_status not in task_ok and tid not in protect_t:
                continue
            t_title = _truncate(str(t.get("title") or ""), _TITLE_MAX_CHARS)
            line = f"  - {tid or '?'} [{t_status or '?'}] {t_title}"
            notes = t.get("notes")
            if notes:
                line += f" — {_truncate(str(notes), _NOTES_MAX_CHARS)}"
            lines.append(line)
        return "\n".join(lines)

    # Prefer keeping protected goals; fill with newest-updated until budget.
    protected = [g for g in candidates if str(g.get("id") or "") in protect_g]
    others = [g for g in candidates if str(g.get("id") or "") not in protect_g]

    # Protected goals always included first (may exceed max_tokens by design so
    # wake-referenced goal/task ids stay visible).
    selected: list[Mapping[str, Any]] = list(protected)
    text = _join_goal_blocks([_render_one(g) for g in selected])

    for g in others:
        trial_blocks = [_render_one(x) for x in selected] + [_render_one(g)]
        trial = _join_goal_blocks(trial_blocks)
        if selected and estimate_tokens(trial) > max_tokens:
            break
        selected.append(g)
        text = trial

    if not selected:
        return _EMPTY_GOALS
    return text


def _join_goal_blocks(blocks: Sequence[str]) -> str:
    return "\n".join(b for b in blocks if b)


def _truncate(text: str, max_chars: int) -> str:
    text = text.replace("\n", " ").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


__all__ = [
    "BIAS_BACKGROUND",
    "BIAS_DO_WORK",
    "BIAS_MOMENT_CONTINUE",
    "BIAS_PLAN_WORK",
    "BIAS_REST",
    "BIAS_TALK",
    "BIAS_TIMER_GENERIC",
    "BIAS_TIMER_LINKED",
    "BIAS_WAIT_TIMEOUT",
    "format_goals_slice",
    "format_skill_bias",
    "format_skill_catalog",
]
