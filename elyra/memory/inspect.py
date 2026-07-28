"""Read-only memory inspection helpers for glass /api/memory/* (PR9).

Scope: serialize meal packages and atoms for operator UI; no mutations.
Out of scope: vector/graph product, atom edit/delete, embedding status UI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from elyra.memory.meal import MealItem, MealPackage
from elyra.memory.store import MemoryStore
from elyra.memory.tokens import estimate_tokens
from elyra.memory.types import (
    ATOM_KINDS,
    PERIOD_SCALE_ORDER,
    Atom,
    atom_to_dict,
    to_iso_z,
    utc_now_iso,
)

# Truncation for glass list rows (not store limits).
_SNIPPET_CHARS = 240
_ATOM_LIST_HARD_CAP = 200
_ATOM_TEXT_CAP = 4000


def truncate_text(text: str | None, *, max_chars: int = _SNIPPET_CHARS) -> str:
    """Return text truncated with ellipsis when over ``max_chars``."""
    if not text:
        return ""
    s = str(text)
    if len(s) <= max_chars:
        return s
    if max_chars <= 1:
        return "…"
    return s[: max_chars - 1] + "…"


def meal_item_to_inspect(item: MealItem) -> dict[str, Any]:
    """JSON-ready meal channel row for the Context inspector."""
    meta = dict(item.meta) if item.meta else {}
    # Drop bulky nested lists from glass payload; keep counts / key ids.
    slim_meta: dict[str, Any] = {}
    for key in (
        "scale",
        "window_start",
        "window_end",
        "moment_id",
        "slid_off",
        "wake_message_id",
    ):
        if key in meta and meta[key] is not None:
            slim_meta[key] = meta[key]
    atom_ids = meta.get("atom_ids")
    if isinstance(atom_ids, (list, tuple)):
        slim_meta["atom_count"] = len(atom_ids)
    media_ids = meta.get("media_ids")
    if isinstance(media_ids, (list, tuple)) and media_ids:
        slim_meta["media_count"] = len(media_ids)
    return {
        "atom_id": item.atom_id,
        "channel": item.channel,
        "label": item.label,
        "role": item.role,
        "token_estimate": int(item.token_estimate),
        "t_start": item.t_start,
        "snippet": truncate_text(item.content, max_chars=_SNIPPET_CHARS),
        "content_chars": len(item.content or ""),
        "meta": slim_meta,
    }


def meal_package_to_inspect(
    package: MealPackage,
    *,
    system_text: str = "",
    orient_text: str = "",
    budget_tokens: int | None = None,
    source: str = "compose",
    recorded_at: str | None = None,
    fixed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a MealPackage for glass Context tab (no secrets)."""
    items = [meal_item_to_inspect(i) for i in package.items]
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        by_channel.setdefault(str(row["channel"]), []).append(row)

    sys_tok = estimate_tokens(system_text) if system_text else 0
    orient_tok = estimate_tokens(orient_text) if orient_text else 0
    fixed_block = dict(fixed) if fixed else {}
    if system_text or sys_tok:
        fixed_block.setdefault(
            "system",
            {
                "label": "system",
                "token_estimate": sys_tok,
                "snippet": truncate_text(system_text, max_chars=120),
                "content_chars": len(system_text or ""),
            },
        )
    if orient_text or orient_tok:
        fixed_block.setdefault(
            "orient",
            {
                "label": "orient",
                "token_estimate": orient_tok,
                "snippet": truncate_text(orient_text, max_chars=_SNIPPET_CHARS),
                "content_chars": len(orient_text or ""),
            },
        )

    channel_totals = {
        ch: sum(int(r["token_estimate"]) for r in rows)
        for ch, rows in by_channel.items()
    }
    return {
        "source": source,
        "recorded_at": recorded_at or utc_now_iso(),
        "open_moment_id": package.open_moment_id,
        "total_tokens": int(package.total_tokens),
        "fixed_tokens": sys_tok + orient_tok,
        "budget_tokens": budget_tokens,
        "slid_off_count": int(package.slid_off_count),
        "compact_text": truncate_text(package.compact_text, max_chars=_SNIPPET_CHARS)
        if package.compact_text
        else None,
        "channels_present": list(package.channels_present),
        "channel_token_totals": channel_totals,
        "fixed": fixed_block,
        "items": items,
        "channels": by_channel,
    }


def atom_to_list_row(atom: Atom) -> dict[str, Any]:
    """Lightweight atom row for Atoms timeline (truncated text)."""
    text = atom.content_text or ""
    return {
        "atom_id": atom.atom_id,
        "kind": atom.kind,
        "moment_id": atom.moment_id,
        "t_start": atom.t_start,
        "t_end": atom.t_end,
        "scale": atom.scale,
        "text": truncate_text(text, max_chars=_SNIPPET_CHARS),
        "text_chars": len(text),
        "prev_atom_id": atom.prev_atom_id,
        "next_atom_id": atom.next_atom_id,
        "embedding_status": atom.embedding_status,
        "media_count": len(atom.media_ids or ()),
    }


def atom_to_detail(atom: Atom) -> dict[str, Any]:
    """Fuller atom payload for drill-down (still no secrets; text capped)."""
    row = atom_to_dict(atom)
    text = row.get("content_text") or ""
    if isinstance(text, str) and len(text) > _ATOM_TEXT_CAP:
        row["content_text"] = truncate_text(text, max_chars=_ATOM_TEXT_CAP)
        row["content_truncated"] = True
    else:
        row["content_truncated"] = False
    # content_ref may be a relative blob path — fine for operators; not a secret.
    return row


def list_atoms_for_glass(
    store: MemoryStore,
    *,
    kind: str | None = None,
    moment_id: str | None = None,
    limit: int = 50,
) -> list[Atom]:
    """Recent atoms newest-first for glass list.

    Prefer sequential weave (walk_prev from global tail). Moment filter uses
    ``list_by_moment``. Summary kind uses ladder indexes. Failures raise to
    caller (API maps to fail-closed).
    """
    lim = max(1, min(int(limit), _ATOM_LIST_HARD_CAP))
    kind_f = kind.strip() if isinstance(kind, str) and kind.strip() else None
    if kind_f is not None and kind_f not in ATOM_KINDS:
        raise ValueError(f"invalid kind: {kind_f!r}")

    mid = moment_id.strip() if isinstance(moment_id, str) and moment_id.strip() else None
    if mid:
        kinds_arg: Sequence[str] | None = (kind_f,) if kind_f else None
        atoms = store.list_by_moment(mid, kinds=kinds_arg)
        atoms = sorted(
            atoms,
            key=lambda a: (to_iso_z(a.t_start), a.atom_id),
            reverse=True,
        )
        return atoms[:lim]

    if kind_f == "summary":
        collected: list[Atom] = []
        for scale in PERIOD_SCALE_ORDER:
            collected.extend(store.list_summaries(scale, limit=lim))
        collected = sorted(
            collected,
            key=lambda a: (to_iso_z(a.t_start or a.window_start or ""), a.atom_id),
            reverse=True,
        )
        # De-dupe by atom_id (summaries may appear once per scale).
        seen: set[str] = set()
        out: list[Atom] = []
        for a in collected:
            if a.atom_id in seen:
                continue
            seen.add(a.atom_id)
            out.append(a)
            if len(out) >= lim:
                break
        return out

    # Sequential weave: newest-first via walk_prev from global tail.
    # Over-fetch when filtering kind so we still fill the page.
    fetch_n = lim if not kind_f else min(lim * 25, _ATOM_LIST_HARD_CAP * 2)
    tail = store.global_tail()
    if tail is None:
        # Fallback: wide range (may be oldest-first limited — only when empty chain).
        return _list_range_newest(store, kinds=(kind_f,) if kind_f else None, limit=lim)

    walked = store.walk_prev(tail.atom_id, n=max(fetch_n, 1))
    if kind_f:
        walked = [a for a in walked if a.kind == kind_f]
    if len(walked) >= lim or not kind_f:
        return walked[:lim]

    # Kind filter sparse on chain — supplement with range scan.
    extra = _list_range_newest(store, kinds=(kind_f,), limit=lim)
    seen_ids = {a.atom_id for a in walked}
    for a in extra:
        if a.atom_id in seen_ids:
            continue
        walked.append(a)
        seen_ids.add(a.atom_id)
        if len(walked) >= lim:
            break
    return walked[:lim]


def _list_range_newest(
    store: MemoryStore,
    *,
    kinds: Sequence[str] | None,
    limit: int,
) -> list[Atom]:
    """Wide list_range then reverse (best-effort when chain empty)."""
    end = datetime.now(UTC) + timedelta(seconds=1)
    start = end - timedelta(days=365)
    rows = store.list_range(
        start,
        end,
        kinds=kinds,
        limit=max(limit, _ATOM_LIST_HARD_CAP),
    )
    rows = sorted(
        rows,
        key=lambda a: (to_iso_z(a.t_start), a.atom_id),
        reverse=True,
    )
    return rows[:limit]


__all__ = [
    "atom_to_detail",
    "atom_to_list_row",
    "list_atoms_for_glass",
    "meal_item_to_inspect",
    "meal_package_to_inspect",
    "truncate_text",
]
