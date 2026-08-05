"""Normative beat → atom promotion rules (Phase 1).

Scope: pure promote_beat / promote_wake_observation + control-kind filters.
In scope: R1–R10, KD16 tool density, ledger one-liners, sequential link,
idempotency. Best-effort: never raise into the do-loop.
Out of scope: doloop/presence hooks, GoalsStore, meal, ladder.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, MutableMapping, Sequence

from elyra.memory.config import MemorySettings
from elyra.memory.parcel import (
    make_parent_and_parcels,
    parcel_threshold,
    should_split_into_parcels,
)
from elyra.memory.store import MemoryStore
from elyra.memory.types import Atom, atom_replace, new_atom_id, to_iso_z, utc_now_iso

_LOG = logging.getLogger(__name__)

# Exact kinds emitted by elyra/loop/doloop.py today (R1).
CONTROL_OBS_KINDS: frozenset[str] = frozenset(
    {
        "continue",
        "no_speak_nudge",
        "answer_speak_nudge",
        "work_continue",
        "skill_commit",
        "tool_skip_identical",
        "tool_thrash",  # thrash HOST inject
        "thrash_lesson",  # thrash lesson request (+ any future thrash_*)
    }
)

LEDGER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "create_goal",
        "create_task",
        "update_goal",
        "update_task",
    }
)

# Future denylist; default empty (load_skill skipped separately per design table).
NON_MEMORABLE_TOOLS: frozenset[str] = frozenset()

# Tools that never promote even when NON_MEMORABLE_TOOLS is empty.
_SKIP_TOOL_NAMES: frozenset[str] = frozenset({"load_skill"})

MODEL_PROMOTE_MIN_CHARS = 40
MEMORY_ATOM_MAX_CHARS = 8000
TOOL_OK_PREVIEW_CHARS = 240
MAX_TOOL_ATOMS_PER_MOMENT = 48  # non-speak, non-ledger; failures exempt after cap

# Observation dedupe window (wake + interjection glass double-write).
_OBS_DEDUPE_SECONDS = 2.0

_META_IDEM = "idempotency_key"
_META_CONTENT_HASH = "content_hash"
_META_MEDIA_FP = "media_fp"


@dataclass
class PromoteState:
    """Per-moment promote counters held by the loop (PR5 wiring).

    ``tool_atoms`` counts non-speak, non-ledger tool atoms promoted this moment
    (R4 soft cap).
    """

    tool_atoms: int = 0
    # Optional set of recent idempotency keys seen this moment (fast path).
    seen_keys: set[str] = field(default_factory=set)


def content_hash(text: str) -> str:
    """Stable short hash of UTF-8 text (idempotency / dedupe)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def is_control_obs_kind(kind: str | None) -> bool:
    """True when an obs kind must never become an atom (R1)."""
    if not kind:
        return False
    if kind in CONTROL_OBS_KINDS:
        return True
    if kind.startswith("thrash"):  # belt-and-suspenders
        return True
    return False


def _settings_or_default(settings: MemorySettings | None) -> MemorySettings:
    return settings if settings is not None else MemorySettings()


def _write_enabled(settings: MemorySettings) -> bool:
    return bool(settings.write_atoms)


def _atom_max(settings: MemorySettings) -> int:
    n = int(settings.atom_max_chars or MEMORY_ATOM_MAX_CHARS)
    return n if n > 0 else MEMORY_ATOM_MAX_CHARS


def _tool_preview(settings: MemorySettings) -> int:
    n = int(settings.tool_ok_preview_chars or TOOL_OK_PREVIEW_CHARS)
    return n if n > 0 else TOOL_OK_PREVIEW_CHARS


def _tool_cap(settings: MemorySettings) -> int:
    n = int(settings.max_tool_atoms_per_moment)
    if n < 0:
        return MAX_TOOL_ATOMS_PER_MOMENT
    return n


def _model_min(settings: MemorySettings) -> int:
    n = int(settings.model_promote_min_chars or MODEL_PROMOTE_MIN_CHARS)
    return max(0, n)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _beat_ts(beat: Mapping[str, Any]) -> str:
    raw = beat.get("ts") or beat.get("t") or beat.get("t_start")
    if isinstance(raw, str) and raw.strip():
        try:
            return to_iso_z(raw)
        except (TypeError, ValueError):
            return raw.strip()
    if isinstance(raw, datetime):
        try:
            return to_iso_z(raw)
        except (TypeError, ValueError):
            pass
    return utc_now_iso()


def _parse_jsonish(content: str) -> Any | None:
    text = (content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _media_ids_from_beat(beat: Mapping[str, Any]) -> tuple[str, ...]:
    raw = beat.get("media_ids")
    if raw is None and isinstance(beat.get("meta"), Mapping):
        raw = beat["meta"].get("media_ids")
    if raw is None:
        # Speak delivery may embed attachment ids in content JSON.
        content = beat.get("content")
        if isinstance(content, str):
            parsed = _parse_jsonish(content)
            if isinstance(parsed, Mapping):
                raw = parsed.get("attachment_ids") or parsed.get("media_ids")
    if not raw:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    try:
        return tuple(str(x) for x in raw if x)
    except TypeError:
        return ()


def _media_fp(media_ids: Sequence[str]) -> str:
    return ",".join(sorted(str(m) for m in media_ids if m))


def _idem_key(moment_id: str, source_beat_ts: str, kind: str, chash: str) -> str:
    return f"{moment_id}|{source_beat_ts}|{kind}|{chash}"


def _wake_idem_key(
    moment_id: str, message_id: str | None, chash: str
) -> str:
    token = message_id if message_id else chash
    return f"{moment_id}|wake|{token}"


def _get_tool_count(state: PromoteState | MutableMapping[str, Any] | None) -> int:
    if state is None:
        return 0
    if isinstance(state, PromoteState):
        return int(state.tool_atoms)
    if isinstance(state, MutableMapping):
        return int(state.get("tool_atoms", 0) or 0)
    return 0


def _inc_tool_count(state: PromoteState | MutableMapping[str, Any] | None) -> None:
    if state is None:
        return
    if isinstance(state, PromoteState):
        state.tool_atoms = int(state.tool_atoms) + 1
        return
    if isinstance(state, MutableMapping):
        state["tool_atoms"] = int(state.get("tool_atoms", 0) or 0) + 1


def _remember_key(
    state: PromoteState | MutableMapping[str, Any] | None, key: str
) -> None:
    if state is None:
        return
    if isinstance(state, PromoteState):
        state.seen_keys.add(key)
        return
    if isinstance(state, MutableMapping):
        seen = state.setdefault("seen_keys", set())
        if isinstance(seen, set):
            seen.add(key)


def _key_seen(
    store: MemoryStore,
    moment_id: str,
    key: str,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> bool:
    if state is not None:
        if isinstance(state, PromoteState) and key in state.seen_keys:
            return True
        if isinstance(state, MutableMapping):
            seen = state.get("seen_keys")
            if isinstance(seen, set) and key in seen:
                return True
    try:
        rows = store.list_by_moment(moment_id)
    except Exception:  # noqa: BLE001 — best-effort
        return False
    for atom in rows:
        if atom.meta.get(_META_IDEM) == key:
            return True
    return False


def _obs_dedupe_hit(
    store: MemoryStore,
    moment_id: str,
    *,
    text: str,
    media_ids: Sequence[str],
    t_start: str,
) -> bool:
    """R3: same moment + content_hash + media fingerprint within 2s → skip."""
    chash = content_hash(text)
    mfp = _media_fp(media_ids)
    try:
        t0 = datetime.fromisoformat(t_start.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        t0 = datetime.now(UTC)
    window = timedelta(seconds=_OBS_DEDUPE_SECONDS)
    try:
        rows = store.list_by_moment(moment_id, kinds=["observation"])
    except Exception:  # noqa: BLE001
        return False
    for atom in rows:
        meta = atom.meta or {}
        if meta.get(_META_CONTENT_HASH) != chash and content_hash(
            atom.content_text or ""
        ) != chash:
            continue
        atom_mfp = meta.get(_META_MEDIA_FP)
        if atom_mfp is None:
            atom_mfp = _media_fp(atom.media_ids)
        if atom_mfp != mfp:
            continue
        try:
            at = datetime.fromisoformat(atom.t_start.replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if abs((at - t0).total_seconds()) <= window.total_seconds():
            return True
    return False


def _embedding_status_for_promote(
    settings: MemorySettings,
    atom: Atom,
) -> str:
    """Return embedding_status for a newly promoted atom (KD16).

    When ``semantic_enabled`` and the atom is embeddable → ``pending``.
    When semantic off or empty/non-embeddable → keep ``none``.
    No embedder import here; enqueue is via store write hooks.
    """
    if not settings.semantic_enabled:
        return "none"
    if atom.kind == "moment_meta":
        return "none"
    text = (atom.content_text or "").strip()
    if text or atom.media_ids:
        return "pending"
    return "none"


def _link_and_put(
    store: MemoryStore,
    atom: Atom,
    *,
    moment_id: str,
    settings: MemorySettings,
) -> Atom:
    """R7 sequential linking then put_atom."""
    # Phase 2: mark pending when semantic on (enqueue via store write hooks).
    emb_status = _embedding_status_for_promote(settings, atom)
    if emb_status != atom.embedding_status:
        atom = atom_replace(atom, embedding_status=emb_status)

    prev: Atom | None = None
    try:
        prev = store.moment_tail(moment_id)
        if prev is None and settings.link_across_moments:
            prev = store.global_tail()
    except Exception:  # noqa: BLE001
        _LOG.exception("memory promote tail lookup failed")
        prev = None

    if prev is not None:
        atom = atom_replace(atom, prev_atom_id=prev.atom_id, next_atom_id=None)

    stored = store.put_atom(atom)
    if prev is not None:
        try:
            store.update_links(prev.atom_id, next_atom_id=stored.atom_id)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "memory promote update_links failed prev=%s new=%s",
                prev.atom_id,
                stored.atom_id,
            )
    return stored


def _put_parcel_children(
    store: MemoryStore,
    children: Sequence[Atom],
    *,
    settings: MemorySettings,
) -> list[Atom]:
    """Put parcel children with sequential prev/next among parcels only.

    Does **not** join the experience weave (``moment_tail`` already excludes
    ``kind=parcel``). Each put still fires write hooks for encode enqueue.

    On mid-chain ``put_atom`` failure: stop, return the partial list (caller
    must reconcile parent meta). Full rollback of an already-linked parent is
    out of scope.
    """
    stored: list[Atom] = []
    prev_id: str | None = None
    for child in children:
        emb_status = _embedding_status_for_promote(settings, child)
        atom = child
        if emb_status != atom.embedding_status:
            atom = atom_replace(atom, embedding_status=emb_status)
        if prev_id is not None:
            atom = atom_replace(atom, prev_atom_id=prev_id, next_atom_id=None)
        try:
            row = store.put_atom(atom)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "memory promote parcel put failed atom_id=%s parent=%s "
                "stored=%s planned=%s",
                atom.atom_id,
                atom.parent_atom_id,
                len(stored),
                len(children),
            )
            break
        if prev_id is not None:
            try:
                store.update_links(prev_id, next_atom_id=row.atom_id)
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "memory promote parcel update_links failed prev=%s new=%s",
                    prev_id,
                    row.atom_id,
                )
        stored.append(row)
        prev_id = row.atom_id
    return stored


def _reconcile_parent_parcel_meta(
    store: MemoryStore,
    parent: Atom,
    *,
    planned_count: int,
    stored_children: Sequence[Atom],
) -> Atom:
    """Rewrite parent (and stored children) meta when parcel puts were partial.

    Parent is already on the experience chain — full rollback is out of scope.
    Meta must not claim ``parcel_count=N`` when fewer parcels exist.
    """
    actual = len(stored_children)
    if actual == planned_count:
        return parent

    _LOG.error(
        "memory promote incomplete parcels parent=%s planned=%s stored=%s; "
        "rewriting meta (rollback out of scope)",
        parent.atom_id,
        planned_count,
        actual,
    )
    meta = dict(parent.meta or {})
    meta["parcel_incomplete"] = True
    meta["parcel_planned_count"] = planned_count
    if actual == 0:
        meta.pop("has_parcels", None)
        meta.pop("parcel_count", None)
        meta.pop("first_parcel_id", None)
        # Only first chunk remains; meal must not treat body as complete.
        meta["truncated"] = True
    else:
        meta["has_parcels"] = True
        meta["parcel_count"] = actual
        meta["first_parcel_id"] = stored_children[0].atom_id

    parent = atom_replace(parent, meta=meta)
    try:
        # Preserve experience prev/next; replace scalar meta only.
        parent = store.put_atom(parent)
    except Exception:  # noqa: BLE001
        _LOG.exception(
            "memory promote failed to rewrite parent parcel meta atom_id=%s",
            parent.atom_id,
        )
        return parent

    for child in stored_children:
        cmeta = dict(child.meta or {})
        if (
            cmeta.get("parcel_count") == actual
            and cmeta.get("parcel_incomplete") is True
        ):
            continue
        cmeta["parcel_count"] = actual
        cmeta["parcel_incomplete"] = True
        cmeta["parcel_planned_count"] = planned_count
        try:
            store.put_atom(atom_replace(child, meta=cmeta))
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "memory promote failed to rewrite parcel meta atom_id=%s",
                child.atom_id,
            )
    return parent


def _link_and_put_with_parcels(
    store: MemoryStore,
    *,
    moment_id: str,
    settings: MemorySettings,
    kind: str,
    raw_text: str,
    t_start: str,
    media_ids: Sequence[str] = (),
    source_beat_ts: str | None = None,
    source_beat_type: str | None = None,
    base_meta: Mapping[str, Any] | None = None,
) -> Atom:
    """Put experience atom; when parcels apply, split before any truncate.

    KD21: parcels run before ``_truncate`` / store cap. Effective chunk size
    is ``min(parcel_threshold_chars, atom_max_chars)``. On split construction
    failure, falls back to Phase 1 single-atom truncate. Mid-chain parcel put
    failures reconcile parent meta (no silent wrong ``parcel_count``).
    """
    meta = dict(base_meta or {})
    if should_split_into_parcels(raw_text, settings):
        try:
            thr = parcel_threshold(settings)
            parent, children = make_parent_and_parcels(
                text=raw_text,
                max_chars=thr,
                kind=kind,
                t_start=t_start,
                moment_id=moment_id,
                media_ids=media_ids,
                source_beat_ts=source_beat_ts,
                source_beat_type=source_beat_type,
                base_meta=meta,
            )
            stored_parent = _link_and_put(
                store, parent, moment_id=moment_id, settings=settings
            )
            if children:
                stored_children = _put_parcel_children(
                    store, children, settings=settings
                )
                if len(stored_children) != len(children):
                    stored_parent = _reconcile_parent_parcel_meta(
                        store,
                        stored_parent,
                        planned_count=len(children),
                        stored_children=stored_children,
                    )
            return stored_parent
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "memory promote parcel split failed; falling back to truncate"
            )

    max_chars = _atom_max(settings)
    body, truncated = _truncate(raw_text, max_chars)
    if truncated:
        meta["truncated"] = True
    atom = Atom(
        atom_id=new_atom_id(),
        t_start=t_start,
        kind=kind,
        content_text=body,
        content_ref="inline",
        moment_id=moment_id,
        media_ids=tuple(str(m) for m in (media_ids or ()) if m),
        source_beat_ts=source_beat_ts,
        source_beat_type=source_beat_type,
        meta=meta,
    )
    return _link_and_put(store, atom, moment_id=moment_id, settings=settings)


def _ledger_one_liner(name: str, content: str, ok: bool) -> str:
    """Best-effort one-liner from ledger tool content JSON (R4)."""
    parsed = _parse_jsonish(content)
    goal: Mapping[str, Any] | None = None
    task: Mapping[str, Any] | None = None
    if isinstance(parsed, Mapping):
        g = parsed.get("goal")
        t = parsed.get("task")
        if isinstance(g, Mapping):
            goal = g
        if isinstance(t, Mapping):
            task = t
        # Some payloads nest under payload key.
        if goal is None and isinstance(parsed.get("payload"), Mapping):
            g2 = parsed["payload"].get("goal")
            if isinstance(g2, Mapping):
                goal = g2
        if task is None and isinstance(parsed.get("payload"), Mapping):
            t2 = parsed["payload"].get("task")
            if isinstance(t2, Mapping):
                task = t2

    if name in ("create_goal", "update_goal") and goal is not None:
        gid = goal.get("id") or goal.get("goal_id") or "?"
        title = str(goal.get("title") or "").strip() or "(untitled)"
        status = str(goal.get("status") or "").strip() or "?"
        return f"goal {gid}: {title} [{status}]"
    if name in ("create_task", "update_task") and task is not None:
        tid = task.get("id") or task.get("task_id") or "?"
        title = str(task.get("title") or "").strip() or "(untitled)"
        status = str(task.get("status") or "").strip() or "?"
        return f"task {tid} → {status}: {title}"

    # Fallback: truncated raw content / short error.
    body = (content or "").strip()
    if not body:
        return f"{name}: {'ok' if ok else 'failed'}"
    if len(body) > 200:
        body = body[:200]
    return body


def _speak_text_from_content(content: str, ok: bool, error_reason: Any) -> str:
    parsed = _parse_jsonish(content)
    if isinstance(parsed, Mapping):
        text = parsed.get("text")
        if isinstance(text, str) and text.strip():
            return text
        if not ok:
            reason = parsed.get("reason") or parsed.get("error_reason") or error_reason
            if reason:
                return f"speak failed: {reason}"
    body = (content or "").strip()
    if body:
        return body
    if not ok and error_reason:
        return f"speak failed: {error_reason}"
    return ""


def _promote_speak(
    store: MemoryStore,
    moment_id: str,
    beat: Mapping[str, Any],
    *,
    settings: MemorySettings,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> Atom | None:
    ok = bool(beat.get("ok"))
    content = str(beat.get("content") or "")
    text = _speak_text_from_content(content, ok, beat.get("error_reason"))
    media_ids = _media_ids_from_beat(beat)
    t_start = _beat_ts(beat)
    kind = "speak"
    # Idempotency over full pre-cap body so parcel and truncate paths share keys.
    chash = content_hash(text)
    key = _idem_key(moment_id, t_start, kind, chash)
    if _key_seen(store, moment_id, key, state):
        return None
    meta: dict[str, Any] = {
        _META_IDEM: key,
        _META_CONTENT_HASH: chash,
        "tool_name": "speak",
        "ok": ok,
        "transport_ok": ok,
    }
    if beat.get("error_reason") is not None:
        meta["error_reason"] = beat.get("error_reason")
    if beat.get("tool_call_id") is not None:
        meta["tool_call_id"] = beat.get("tool_call_id")
    if not ok:
        meta["transport_ok"] = False

    stored = _link_and_put_with_parcels(
        store,
        moment_id=moment_id,
        settings=settings,
        kind=kind,
        raw_text=text,
        t_start=t_start,
        media_ids=media_ids,
        source_beat_ts=t_start,
        source_beat_type="tool",
        base_meta=meta,
    )
    _remember_key(state, key)
    return stored


def _promote_ledger(
    store: MemoryStore,
    moment_id: str,
    beat: Mapping[str, Any],
    *,
    settings: MemorySettings,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> Atom | None:
    name = str(beat.get("name") or "")
    ok = bool(beat.get("ok"))
    content = str(beat.get("content") or "")
    one = _ledger_one_liner(name, content, ok)
    t_start = _beat_ts(beat)
    kind = "ledger"
    chash = content_hash(one)
    key = _idem_key(moment_id, t_start, kind, chash)
    if _key_seen(store, moment_id, key, state):
        return None
    meta: dict[str, Any] = {
        _META_IDEM: key,
        _META_CONTENT_HASH: chash,
        "tool_name": name,
        "ok": ok,
    }
    if beat.get("error_reason") is not None:
        meta["error_reason"] = beat.get("error_reason")

    stored = _link_and_put_with_parcels(
        store,
        moment_id=moment_id,
        settings=settings,
        kind=kind,
        raw_text=one,
        t_start=t_start,
        source_beat_ts=t_start,
        source_beat_type="tool",
        base_meta=meta,
    )
    _remember_key(state, key)
    return stored


def _promote_tool(
    store: MemoryStore,
    moment_id: str,
    beat: Mapping[str, Any],
    *,
    settings: MemorySettings,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> Atom | None:
    name = str(beat.get("name") or "")
    if name in NON_MEMORABLE_TOOLS or name in _SKIP_TOOL_NAMES:
        return None
    ok = bool(beat.get("ok"))
    content = str(beat.get("content") or "").strip()
    if not content:
        return None

    # Soft cap: beyond max, only failures still promote (R4 / KD16).
    count = _get_tool_count(state)
    cap = _tool_cap(settings)
    if cap > 0 and count >= cap and ok:
        return None

    t_start = _beat_ts(beat)
    kind = "tool"
    # Tool OK stays density-capped (preview); only failures may parcel full body.
    if ok:
        preview = _tool_preview(settings)
        body, truncated = _truncate(content, preview)
        chash = content_hash(body)
        key = _idem_key(moment_id, t_start, kind, chash)
        if _key_seen(store, moment_id, key, state):
            return None
        meta: dict[str, Any] = {
            _META_IDEM: key,
            _META_CONTENT_HASH: chash,
            "tool_name": name,
            "ok": ok,
            "preview": True,
        }
        if truncated:
            meta["truncated"] = True
        if beat.get("error_reason") is not None:
            meta["error_reason"] = beat.get("error_reason")
        if beat.get("hop") is not None:
            meta["hop"] = beat.get("hop")
        if beat.get("tool_call_id") is not None:
            meta["tool_call_id"] = beat.get("tool_call_id")
        atom = Atom(
            atom_id=new_atom_id(),
            t_start=t_start,
            kind=kind,
            content_text=body,
            content_ref="inline",
            moment_id=moment_id,
            source_beat_ts=t_start,
            source_beat_type="tool",
            meta=meta,
        )
        stored = _link_and_put(store, atom, moment_id=moment_id, settings=settings)
        _remember_key(state, key)
        _inc_tool_count(state)
        return stored

    chash = content_hash(content)
    key = _idem_key(moment_id, t_start, kind, chash)
    if _key_seen(store, moment_id, key, state):
        return None
    meta = {
        _META_IDEM: key,
        _META_CONTENT_HASH: chash,
        "tool_name": name,
        "ok": ok,
    }
    if beat.get("error_reason") is not None:
        meta["error_reason"] = beat.get("error_reason")
    if beat.get("hop") is not None:
        meta["hop"] = beat.get("hop")
    if beat.get("tool_call_id") is not None:
        meta["tool_call_id"] = beat.get("tool_call_id")
    stored = _link_and_put_with_parcels(
        store,
        moment_id=moment_id,
        settings=settings,
        kind=kind,
        raw_text=content,
        t_start=t_start,
        source_beat_ts=t_start,
        source_beat_type="tool",
        base_meta=meta,
    )
    _remember_key(state, key)
    _inc_tool_count(state)
    return stored


def _promote_model(
    store: MemoryStore,
    moment_id: str,
    beat: Mapping[str, Any],
    *,
    settings: MemorySettings,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> Atom | None:
    tool_calls = beat.get("tool_calls")
    if tool_calls:
        return None
    content = str(beat.get("content") or "").strip()
    # Never promote reasoning_content (R1) — strip; not stored on atom body.
    if not content:
        return None
    if len(content) < _model_min(settings):
        return None
    # Skip explicit host-echo markers if present on the beat.
    if beat.get("echo_of_host") or beat.get("host_echo"):
        return None

    t_start = _beat_ts(beat)
    kind = "model"
    chash = content_hash(content)
    key = _idem_key(moment_id, t_start, kind, chash)
    if _key_seen(store, moment_id, key, state):
        return None
    meta: dict[str, Any] = {
        _META_IDEM: key,
        _META_CONTENT_HASH: chash,
    }
    if beat.get("hop") is not None:
        meta["hop"] = beat.get("hop")

    stored = _link_and_put_with_parcels(
        store,
        moment_id=moment_id,
        settings=settings,
        kind=kind,
        raw_text=content,
        t_start=t_start,
        source_beat_ts=t_start,
        source_beat_type="model",
        base_meta=meta,
    )
    _remember_key(state, key)
    return stored


def _promote_interjection(
    store: MemoryStore,
    moment_id: str,
    beat: Mapping[str, Any],
    *,
    settings: MemorySettings,
    state: PromoteState | MutableMapping[str, Any] | None,
) -> Atom | None:
    text = str(beat.get("content") or "").strip()
    media_ids = _media_ids_from_beat(beat)
    if not text and not media_ids:
        return None
    t_start = _beat_ts(beat)
    if _obs_dedupe_hit(
        store, moment_id, text=text, media_ids=media_ids, t_start=t_start
    ):
        return None
    kind = "observation"
    chash = content_hash(text)
    key = _idem_key(moment_id, t_start, kind, chash)
    if _key_seen(store, moment_id, key, state):
        return None
    meta: dict[str, Any] = {
        _META_IDEM: key,
        _META_CONTENT_HASH: chash,
        _META_MEDIA_FP: _media_fp(media_ids),
        "obs_kind": "interjection",
    }

    stored = _link_and_put_with_parcels(
        store,
        moment_id=moment_id,
        settings=settings,
        kind=kind,
        raw_text=text,
        t_start=t_start,
        media_ids=media_ids,
        source_beat_ts=t_start,
        source_beat_type="obs",
        base_meta=meta,
    )
    _remember_key(state, key)
    return stored


def promote_beat(
    store: MemoryStore | None,
    moment_id: str,
    beat: Mapping[str, Any] | None,
    *,
    settings: MemorySettings | None = None,
    moment_tool_counts: PromoteState | MutableMapping[str, Any] | None = None,
) -> Atom | None:
    """Promote a single tape beat to an atom when rules fire (R1–R10).

    Pure w.r.t. GoalsStore / wake claim policy. Best-effort: logs and returns
    None on errors; never raises into the caller.
    """
    cfg = _settings_or_default(settings)
    if store is None or not moment_id or not _write_enabled(cfg):
        return None
    if not isinstance(beat, Mapping):
        return None

    try:
        btype = str(beat.get("type") or "")

        if btype == "obs":
            kind = beat.get("kind")
            kind_s = str(kind) if kind is not None else None
            if is_control_obs_kind(kind_s):
                return None
            if kind_s == "interjection":
                return _promote_interjection(
                    store,
                    moment_id,
                    beat,
                    settings=cfg,
                    state=moment_tool_counts,
                )
            # lesson_pin and other non-control obs: not promoted by default.
            return None

        if btype == "tool":
            name = str(beat.get("name") or "")
            if name == "speak":
                return _promote_speak(
                    store,
                    moment_id,
                    beat,
                    settings=cfg,
                    state=moment_tool_counts,
                )
            if name in LEDGER_TOOL_NAMES:
                return _promote_ledger(
                    store,
                    moment_id,
                    beat,
                    settings=cfg,
                    state=moment_tool_counts,
                )
            return _promote_tool(
                store,
                moment_id,
                beat,
                settings=cfg,
                state=moment_tool_counts,
            )

        if btype == "model":
            return _promote_model(
                store,
                moment_id,
                beat,
                settings=cfg,
                state=moment_tool_counts,
            )

        # stop / skill_load / unknown — no promote by default (R6 empty ok).
        return None
    except Exception:  # noqa: BLE001 — never raise into do-loop
        _LOG.exception(
            "memory promote_beat failed moment_id=%s type=%s",
            moment_id,
            (beat or {}).get("type") if isinstance(beat, Mapping) else None,
        )
        return None


def promote_wake_observation(
    store: MemoryStore | None,
    moment_id: str,
    *,
    content: str | None,
    message_id: str | None = None,
    media_ids: Sequence[str] = (),
    why_now: str = "",
    settings: MemorySettings | None = None,
) -> Atom | None:
    """Promote a social wake user observation (call once at moment open).

    - Promote when content strip non-empty OR media_ids non-empty (media-only OK).
    - meta.wake_message_id = message_id
    - Returns None on dedupe / empty / write_atoms false / errors (errors logged).
    """
    cfg = _settings_or_default(settings)
    if store is None or not moment_id or not _write_enabled(cfg):
        return None

    try:
        text = (content or "").strip()
        mids = tuple(str(m) for m in (media_ids or ()) if m)
        if not text and not mids:
            return None

        # Non-social wakes must not call this (caller's job). why_now is recorded
        # only as meta for meal/debug — no GoalsStore staleness checks (R6).
        t_start = utc_now_iso()
        if _obs_dedupe_hit(
            store, moment_id, text=text, media_ids=mids, t_start=t_start
        ):
            return None

        chash = content_hash(text if text else _media_fp(mids))
        key = _wake_idem_key(moment_id, message_id, chash)
        if _key_seen(store, moment_id, key, None):
            return None

        meta: dict[str, Any] = {
            _META_IDEM: key,
            _META_CONTENT_HASH: chash,
            _META_MEDIA_FP: _media_fp(mids),
            "source": "wake",
        }
        if message_id:
            meta["wake_message_id"] = message_id
        if why_now:
            meta["why_now"] = why_now

        return _link_and_put_with_parcels(
            store,
            moment_id=moment_id,
            settings=cfg,
            kind="observation",
            raw_text=text,
            t_start=t_start,
            media_ids=mids,
            source_beat_ts=t_start,
            source_beat_type="wake",
            base_meta=meta,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception(
            "memory promote_wake_observation failed moment_id=%s", moment_id
        )
        return None


def _view_idem_key(moment_id: str, media_ids: Sequence[str]) -> str:
    """Stable first-wins key for a view observation (moment + media fingerprint)."""
    return f"view:{moment_id}:{_media_fp(media_ids)}"


def promote_view_observation(
    store: MemoryStore | None,
    moment_id: str,
    *,
    media_ids: Sequence[str] = (),
    note: str | None = None,
    source_url: str | None = None,
    settings: MemorySettings | None = None,
) -> Atom | None:
    """First-wins observation breadcrumb for ``view_media`` (KD-V11 / KD-V16).

    - Requires non-empty media_ids (optional note caption).
    - meta.source = ``view_media``, meta.view = true.
    - **Never** stamps ``wake_message_id`` (KD-V16).
    - First-wins: same moment + media fingerprint idempotency key → skip.
    - Returns None on dedupe / empty / write_atoms false / errors (logged).
    """
    cfg = _settings_or_default(settings)
    if store is None or not moment_id or not _write_enabled(cfg):
        return None

    try:
        mids = tuple(str(m) for m in (media_ids or ()) if m)
        if not mids:
            return None
        text = (note or "").strip()
        t_start = utc_now_iso()
        chash = content_hash(text if text else _media_fp(mids))
        key = _view_idem_key(moment_id, mids)
        if _key_seen(store, moment_id, key, None):
            return None

        meta: dict[str, Any] = {
            _META_IDEM: key,
            _META_CONTENT_HASH: chash,
            _META_MEDIA_FP: _media_fp(mids),
            "source": "view_media",
            "view": True,
        }
        if source_url:
            meta["source_url"] = str(source_url)

        return _link_and_put_with_parcels(
            store,
            moment_id=moment_id,
            settings=cfg,
            kind="observation",
            raw_text=text,
            t_start=t_start,
            media_ids=mids,
            source_beat_ts=t_start,
            source_beat_type="view_media",
            base_meta=meta,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception(
            "memory promote_view_observation failed moment_id=%s", moment_id
        )
        return None


__all__ = [
    "CONTROL_OBS_KINDS",
    "LEDGER_TOOL_NAMES",
    "MAX_TOOL_ATOMS_PER_MOMENT",
    "MEMORY_ATOM_MAX_CHARS",
    "MODEL_PROMOTE_MIN_CHARS",
    "NON_MEMORABLE_TOOLS",
    "PromoteState",
    "TOOL_OK_PREVIEW_CHARS",
    "content_hash",
    "is_control_obs_kind",
    "promote_beat",
    "promote_view_observation",
    "promote_wake_observation",
]
