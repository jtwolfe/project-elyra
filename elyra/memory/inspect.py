"""Read-only memory inspection helpers for glass /api/memory/* (PR9 + Phase 2).

Scope: serialize meal packages, atoms, vector/neighbor inspect, and Phase 2a
Graph tab session/neighbor views for the operator UI.
Out of scope: atom edit/delete, raw 2048-d dumps, edge mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from elyra.memory.meal import MealItem, MealPackage
from elyra.memory.store import MemoryStore
from elyra.memory.tokens import estimate_tokens
from elyra.memory.types import (
    ATOM_KINDS,
    EMBEDDING_STATUSES,
    PERIOD_SCALE_ORDER,
    Atom,
    atom_to_dict,
    parse_iso_z,
    to_iso_z,
    utc_now_iso,
)
from elyra.memory.weights import (
    BASE_CREATED_WITH,
    BASE_HAS_CHANNEL,
    BASE_IN_MOMENT,
    BASE_PARENT_CHILD,
    BASE_RECALLS,
    BASE_SAME_MOMENT,
    BASE_SEMANTIC_HOP,
    BASE_SEQUENTIAL,
    BASE_SUMMARY_CHILD,
    BASE_SUMMARY_SOURCE,
    BASE_SUPERSEDES,
    EDGE_CHILD_OF,
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_KINDS,
    EDGE_PARENT_OF,
    EDGE_RECALLS,
    EDGE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP,
    EDGE_SEQUENTIAL,
    EDGE_SUMMARY_CHILD,
    EDGE_SUMMARY_SOURCE,
    EDGE_SUPERSEDES,
    base_weight,
)

# Truncation for glass list rows (not store limits).
# Glass Context inspect preview (BUG-mem-ui-01: honesty labels when truncated).
# Not the stored summary cap — only what operators see in /api/memory/context.
_SNIPPET_CHARS = 480
_ATOM_LIST_HARD_CAP = 200
_ATOM_TEXT_CAP = 4000
_NEIGHBOR_K_DEFAULT = 12
_NEIGHBOR_K_MAX = 50


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
        # Cap for Glass Context multi-atom inspect (BUG-mem-ui-01).
        ids_out: list[str] = []
        for raw_id in atom_ids:
            if not isinstance(raw_id, str):
                continue
            s = raw_id.strip()
            if s:
                ids_out.append(s)
            if len(ids_out) >= 24:
                break
        if ids_out:
            slim_meta["atom_ids"] = ids_out
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
        "semantic_omitted_reason": getattr(
            package, "semantic_omitted_reason", None
        ),
        "semantic_select_meta": getattr(
            package, "semantic_select_meta", None
        ),
        "directed_keep_omitted_reason": getattr(
            package, "directed_keep_omitted_reason", None
        ),
        "directed_keep_meta": getattr(package, "directed_keep_meta", None),
        "glass_tail_meta": getattr(package, "glass_tail_meta", None),
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


def atom_to_detail(
    atom: Atom,
    *,
    media_store: Any | None = None,
) -> dict[str, Any]:
    """Fuller atom payload for drill-down (still no secrets; text capped).

    Promotes encode diagnostics from ``atom.meta`` for glass Atoms detail
    (KD-M3): ``embed_channels``, ``embed_error``, ``embed_media_skipped``.
    Best-effort media inventory (id / kind / mime / filename / url) when
    ``media_store`` is provided; never includes blob bytes or secrets.
    """
    row = atom_to_dict(atom)
    text = row.get("content_text") or ""
    if isinstance(text, str) and len(text) > _ATOM_TEXT_CAP:
        row["content_text"] = truncate_text(text, max_chars=_ATOM_TEXT_CAP)
        row["content_truncated"] = True
    else:
        row["content_truncated"] = False
    # content_ref may be a relative blob path — fine for operators; not a secret.

    meta = atom.meta or {}
    channels = meta.get("embed_channels")
    if isinstance(channels, (list, tuple)):
        row["embed_channels"] = [str(c) for c in channels if c]
    else:
        row["embed_channels"] = []
    err = meta.get("embed_error")
    row["embed_error"] = str(err) if err else None
    skipped = meta.get("embed_media_skipped")
    if isinstance(skipped, (list, tuple)):
        row["embed_media_skipped"] = [str(s) for s in skipped if s is not None]
    else:
        row["embed_media_skipped"] = []

    # Optional media inventory for glass (no blob bytes).
    media_ids = list(atom.media_ids or ())
    inventory: list[dict[str, Any]] = []
    for mid in media_ids:
        if not mid:
            continue
        entry: dict[str, Any] = {
            "id": str(mid),
            "kind": None,
            "mime": None,
            "filename": None,
            "url": f"/api/media/{mid}",
        }
        if media_store is not None:
            try:
                get_fn = getattr(media_store, "get", None)
                att = get_fn(str(mid)) if callable(get_fn) else None
                if att is not None:
                    entry["kind"] = getattr(att, "kind", None) or None
                    entry["mime"] = getattr(att, "mime", None) or None
                    entry["filename"] = getattr(att, "filename", None) or None
            except Exception:  # noqa: BLE001 — inventory is best-effort
                pass
        inventory.append(entry)
    row["media"] = inventory
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


# ── Vectors / neighbors (Phase 2 PR7) ─────────────────────────────────────


def _embed_channels_from_atom(atom: Atom) -> list[str]:
    """Channel names present for atom (meta or status-only hint)."""
    meta = atom.meta or {}
    raw = meta.get("embed_channels")
    if isinstance(raw, (list, tuple)):
        return [str(c) for c in raw if c]
    return []


def atom_to_vector_row(atom: Atom) -> dict[str, Any]:
    """Atom row for Vectors status list (embedding-focused, no secrets)."""
    row = atom_to_list_row(atom)
    row["channels"] = _embed_channels_from_atom(atom)
    meta = atom.meta or {}
    if meta.get("embed_error"):
        row["embed_error"] = str(meta.get("embed_error"))
    if meta.get("embed_model"):
        row["embed_model"] = str(meta.get("embed_model"))
    if meta.get("embed_encoded_at"):
        row["encoded_at"] = str(meta.get("embed_encoded_at"))
    # Partial-encode honesty for Vectors list (KD-M3); glass may badge chips.
    skipped = meta.get("embed_media_skipped")
    if isinstance(skipped, (list, tuple)) and skipped:
        row["embed_media_skipped"] = [str(s) for s in skipped if s is not None]
    return row


def list_atoms_by_embedding_status(
    store: MemoryStore,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[Atom]:
    """List atoms filtered by ``embedding_status`` via Protocol ``list_atoms``.

    ``status`` None / empty / ``all`` → no status filter. Invalid status raises
    ``ValueError``. Hard-capped at ``_ATOM_LIST_HARD_CAP``.
    """
    lim = max(1, min(int(limit), _ATOM_LIST_HARD_CAP))
    status_f: str | None = None
    if isinstance(status, str) and status.strip():
        status_f = status.strip().lower()
        if status_f in ("all", "*"):
            status_f = None
        elif status_f not in EMBEDDING_STATUSES:
            raise ValueError(f"invalid embedding_status: {status!r}")

    list_fn = getattr(store, "list_atoms", None)
    if not callable(list_fn):
        raise RuntimeError("store does not support list_atoms")
    return list(
        list_fn(
            embedding_status=status_f,
            limit=lim,
            newest_first=True,
        )
    )


def neighbor_hit_to_inspect(hit: Any) -> dict[str, Any]:
    """Serialize an EmbeddingIndex ``ScoredAtom`` (or duck-type) for glass.

    ``score`` is cosine similarity (higher is closer). ``score_kind`` is always
    ``"cosine"`` so glass can badge honestly without guessing.
    """
    atom = getattr(hit, "atom", None)
    atom_id = str(getattr(hit, "atom_id", "") or "")
    score = getattr(hit, "score", None)
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    channel = str(getattr(hit, "channel", "joint") or "joint")
    base = {
        "atom_id": atom_id,
        "score": score_f,
        "score_kind": "cosine",
        "channel": channel,
        "kind": None,
        "moment_id": None,
        "t_start": None,
        "embedding_status": None,
        "snippet": "",
        "text_chars": 0,
    }
    if atom is not None:
        text = getattr(atom, "content_text", None) or ""
        base.update(
            {
                "atom_id": atom_id or getattr(atom, "atom_id", ""),
                "kind": getattr(atom, "kind", None),
                "moment_id": getattr(atom, "moment_id", None),
                "t_start": getattr(atom, "t_start", None),
                "embedding_status": getattr(atom, "embedding_status", None),
                "snippet": truncate_text(str(text), max_chars=_SNIPPET_CHARS),
                "text_chars": len(str(text)),
            }
        )
    return base


def resolve_neighbor_k(raw: Any, *, default: int = _NEIGHBOR_K_DEFAULT) -> int:
    """Clamp top-k for neighbor inspect."""
    try:
        k = int(raw)
    except (TypeError, ValueError):
        k = default
    return max(1, min(k, _NEIGHBOR_K_MAX))


def query_vector_for_atom(
    atom_id: str,
    *,
    index: Any | None,
    store: MemoryStore | None,
    channel: str = "joint",
) -> tuple[list[float] | None, str | None]:
    """Best-effort query vector for seed atom (no raw dump to callers beyond list).

    Prefers in-memory index ``get``; falls back to store ``get_vectors``.
    Returns ``(vector, error_reason)``.

    **Channel alignment (PR-R5 / KD-R16):** load the vector for the **concrete**
    ``channel`` only. Never soft-fallback to another embed column — callers that
    want auto-policy must ``resolve_search_channel`` first and pass the concrete
    channel here so query and corpus search stay aligned.
    """
    emb = None
    if index is not None:
        get_fn = getattr(index, "get", None)
        if callable(get_fn):
            try:
                emb = get_fn(atom_id)
            except Exception:  # noqa: BLE001
                emb = None
    if emb is None and store is not None:
        get_v = getattr(store, "get_vectors", None)
        if callable(get_v):
            try:
                emb = get_v(atom_id)
            except Exception:  # noqa: BLE001
                emb = None
    if emb is None:
        return None, "no_vector"
    ch = (channel or "joint").strip().lower() or "joint"
    try:
        vec = emb.channel_vector(ch)
    except Exception:  # noqa: BLE001
        vec = None
    if vec is not None:
        return list(vec), None
    return None, "no_vector"


def _empty_depth_by_priority() -> dict[str, int]:
    """Zero queue depths for bulk lanes (glass / Vectors defaults)."""
    return {"atom_create": 0, "catchup": 0}


def _slim_last_drain_stats(raw: Any) -> dict[str, Any] | None:
    """Keep only non-secret drain counters for health (ok/failed/processed/ms)."""
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, Any] = {
        "ok": int(raw.get("ok") or 0),
        "failed": int(raw.get("failed") or 0),
        "processed": int(raw.get("processed") or 0),
    }
    if raw.get("ms") is not None:
        try:
            out["ms"] = int(raw.get("ms") or 0)
        except (TypeError, ValueError):
            out["ms"] = 0
    if raw.get("skipped") is not None:
        try:
            out["skipped"] = int(raw.get("skipped") or 0)
        except (TypeError, ValueError):
            pass
    if raw.get("remaining") is not None:
        try:
            out["remaining"] = int(raw.get("remaining") or 0)
        except (TypeError, ValueError):
            pass
    return out


def encode_worker_health_block(
    presence: Any | None = None,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """JSON-ready continuous-encode worker + gate metrics (no secrets).

    Reads process-local counters from a PresenceWorker-like object when provided.
    Safe defaults when continuous encode is off or presence is unavailable.
    Never includes atom content, atom_ids, or secrets.
    """
    cfg = settings
    if cfg is None and presence is not None:
        cfg = getattr(getattr(presence, "settings", None), "memory", None)
    enabled = bool(getattr(cfg, "encode_worker_enabled", True)) if cfg else True
    empty: dict[str, Any] = {
        "enabled": enabled,
        "owner": "none",
        "alive": False,
        "restarts": 0,
        "restart_throttled": False,
        "gap_drain_active": False,
        "last_drain_at": None,
        "last_drain_stats": None,
        "drain_ok_total": 0,
        "drain_failed_total": 0,
        "gate_lookup_waits": 0,
        "gate_lookup_wait_ms_last": 0,
        "gate_bulk_yields": 0,
        "embedder_state": "absent",
    }
    if presence is None:
        return empty

    try:
        owner = str(getattr(presence, "_encode_owner", None) or "none")
        if owner not in ("none", "idle", "worker"):
            owner = "none"
        worker = getattr(presence, "_encode_worker", None)
        alive = False
        if worker is not None:
            try:
                is_alive = getattr(worker, "is_alive", None)
                alive = bool(is_alive()) if callable(is_alive) else False
            except Exception:  # noqa: BLE001
                alive = False

        last_at = getattr(presence, "_encode_last_drain_at", None)
        # Health expects wall-clock ISO; ignore monotonic floats from older runs.
        if isinstance(last_at, str) and last_at.strip():
            last_drain_at: str | None = last_at.strip()
        else:
            last_drain_at = None

        gate = getattr(presence, "_embedder_gate", None)
        gate_lookup_waits = 0
        gate_lookup_wait_ms_last = 0
        gate_bulk_yields = 0
        if gate is not None:
            try:
                gate_lookup_waits = int(getattr(gate, "gate_lookup_waits", 0) or 0)
            except (TypeError, ValueError):
                gate_lookup_waits = 0
            try:
                gate_lookup_wait_ms_last = int(
                    getattr(gate, "gate_lookup_wait_ms_last", 0) or 0
                )
            except (TypeError, ValueError):
                gate_lookup_wait_ms_last = 0
            try:
                gate_bulk_yields = int(getattr(gate, "gate_bulk_yields", 0) or 0)
            except (TypeError, ValueError):
                gate_bulk_yields = 0

        emb_state = str(getattr(presence, "_embedder_state", None) or "absent")
        if emb_state not in ("absent", "loading", "warm", "failed"):
            emb_state = "absent"

        return {
            "enabled": enabled,
            "owner": owner,
            "alive": alive,
            "restarts": int(getattr(presence, "_encode_worker_restarts", 0) or 0),
            "restart_throttled": bool(
                getattr(presence, "_encode_worker_restart_throttled", False)
            ),
            "gap_drain_active": bool(getattr(presence, "_gap_drain_active", False)),
            "last_drain_at": last_drain_at,
            "last_drain_stats": _slim_last_drain_stats(
                getattr(presence, "_encode_last_drain_stats", None)
            ),
            "drain_ok_total": int(
                getattr(presence, "_encode_drain_ok_total", 0) or 0
            ),
            "drain_failed_total": int(
                getattr(presence, "_encode_drain_failed_total", 0) or 0
            ),
            "gate_lookup_waits": gate_lookup_waits,
            "gate_lookup_wait_ms_last": gate_lookup_wait_ms_last,
            "gate_bulk_yields": gate_bulk_yields,
            "embedder_state": emb_state,
        }
    except Exception:  # noqa: BLE001 — never break Vectors overview
        return empty


def encoder_health_block(
    *,
    settings: Any | None,
    embedder: Any | None,
    queue: Any | None,
    presence: Any | None = None,
) -> dict[str, Any]:
    """JSON-ready encoder + queue + encode-worker health for Vectors (no secrets)."""
    cfg = settings
    embed_enabled = bool(getattr(cfg, "embed_enabled", False)) if cfg else False
    semantic_enabled = bool(getattr(cfg, "semantic_enabled", False)) if cfg else False
    backend = str(getattr(cfg, "embed_backend", "mock") or "mock") if cfg else "mock"
    device_pref = str(getattr(cfg, "embed_device", "auto") or "auto") if cfg else "auto"
    model_pin = str(getattr(cfg, "embed_model_id", "") or "") if cfg else ""

    block: dict[str, Any] = {
        "ok": False,
        "embed_enabled": embed_enabled,
        "semantic_enabled": semantic_enabled,
        "backend": backend,
        "device": None,
        "device_pref": device_pref,
        "model_id": model_pin or None,
        "dim": None,
        "loaded": embedder is not None,
        # media_encode: can this instance accept media inputs (KD-M4)?
        # Null until embedder health is read; derived below when absent.
        "media_encode": None,
        "queue_depth": 0,
        "queue_depth_by_priority": _empty_depth_by_priority(),
        "queue_max": int(getattr(cfg, "encode_queue_max", 1024) or 1024)
        if cfg
        else 1024,
        "queue_dropped": 0,
        "encode_worker": encode_worker_health_block(presence, settings=cfg),
        "error": None,
    }
    if queue is not None:
        try:
            qsize = getattr(queue, "qsize", None)
            block["queue_depth"] = int(qsize()) if callable(qsize) else int(len(queue))
        except Exception:  # noqa: BLE001
            block["queue_depth"] = 0
        try:
            dropped = getattr(queue, "dropped_total", None)
            block["queue_dropped"] = int(dropped()) if callable(dropped) else int(dropped or 0)
        except Exception:  # noqa: BLE001
            block["queue_dropped"] = 0
        try:
            mx = getattr(queue, "maxsize", None)
            if mx is not None:
                block["queue_max"] = int(mx)
        except Exception:  # noqa: BLE001
            pass
        try:
            depth_fn = getattr(queue, "depth_by_priority", None)
            if callable(depth_fn):
                raw_depths = depth_fn()
                if isinstance(raw_depths, Mapping):
                    depths = _empty_depth_by_priority()
                    for key in depths:
                        try:
                            depths[key] = int(raw_depths.get(key) or 0)
                        except (TypeError, ValueError):
                            depths[key] = 0
                    # Preserve any unexpected lane keys as ints (forward-compat).
                    for key, val in raw_depths.items():
                        sk = str(key)
                        if sk not in depths:
                            try:
                                depths[sk] = int(val or 0)
                            except (TypeError, ValueError):
                                depths[sk] = 0
                    block["queue_depth_by_priority"] = depths
        except Exception:  # noqa: BLE001
            block["queue_depth_by_priority"] = _empty_depth_by_priority()

    if embedder is None:
        if not embed_enabled:
            block["error"] = "embed_disabled"
        else:
            block["error"] = "encoder_not_loaded"
        # No embedder → media encode unavailable (honest null / false).
        block["media_encode"] = False
        return block

    try:
        health = embedder.health() if hasattr(embedder, "health") else {}
        if not isinstance(health, Mapping):
            health = {}
        block["ok"] = bool(health.get("ok", True))
        block["device"] = health.get("device")
        block["model_id"] = health.get("model_id") or block["model_id"]
        block["dim"] = health.get("dim")
        if health.get("backend"):
            block["backend"] = health.get("backend")
        if health.get("error"):
            block["error"] = health.get("error")
        # Surface requested_* notes from unavailable-or-mock wrapper.
        for key in ("requested_backend", "requested_model_id", "note"):
            if key in health and health[key] is not None:
                block[key] = health[key]
        # KD-M4: promote media_encode; derive when health omits the key.
        if "media_encode" in health:
            me = health.get("media_encode")
            block["media_encode"] = None if me is None else bool(me)
        else:
            be = str(block.get("backend") or "").lower()
            if be == "mock":
                block["media_encode"] = bool(block.get("ok"))
            elif be == "nemotron":
                # Unloaded / unknown — null (not a false claim).
                block["media_encode"] = None
            else:
                block["media_encode"] = None
        # Optional tooltip honesty for glass (mock fallback vs real omni).
        # Do not claim "install qwen-omni-utils" for closed mock / non-nemotron.
        be_eff = str(block.get("backend") or "").lower()
        err_s = str(block.get("error") or "")
        if block.get("media_encode") is True:
            if be_eff == "mock":
                block["media_encode_note"] = "mock"
            elif be_eff == "nemotron":
                block["media_encode_note"] = "nemotron_mm_utils"
        elif block.get("media_encode") is False:
            if be_eff == "nemotron" or "mm_utils" in err_s or "qwen_omni" in err_s:
                block["media_encode_note"] = "install_qwen_omni_utils"
            # else: closed mock / encoder absent already returned / unknown — omit
    except Exception as exc:  # noqa: BLE001
        block["ok"] = False
        block["error"] = str(exc) or type(exc).__name__
        block["media_encode"] = None
    return block


def _empty_vectors_by_channel() -> dict[str, int]:
    """Zero counts for durable embed channels (glass defaults; no index import)."""
    return {"joint": 0, "text": 0, "image": 0, "audio": 0, "video": 0}


def index_health_block(index: Any | None) -> dict[str, Any]:
    """JSON-ready EmbeddingIndex health for Vectors overview.

    Always includes honesty fields used by the Vectors glass (PR-R5):
    ``vectors_by_channel``, ``joint_repair_remaining``, ``ann_index_built``,
    ``last_optimize_notes``, ``search_mode``. Defaults are zero/false — never
    claim ready without data.
    """
    empty_counts = _empty_vectors_by_channel()
    if index is None:
        return {
            "ok": False,
            "backend": "none",
            "vectors_ready": 0,
            "index_stale": False,
            "recent_buffer": 0,
            "vectors": False,
            "ann_index_built": False,
            "search_mode": None,
            "last_optimize": None,
            "last_optimize_notes": [],
            "vectors_by_channel": empty_counts,
            "joint_repair_remaining": 0,
            "joint_repair_last_batch": 0,
            "error": "no_index",
        }
    try:
        health = index.health() if hasattr(index, "health") else {}
        if not isinstance(health, Mapping):
            health = {}
        out = dict(health)
        out.setdefault("ok", True)
        out.setdefault("vectors_ready", 0)
        out.setdefault("index_stale", False)
        out.setdefault("recent_buffer", 0)
        out.setdefault("ann_index_built", False)
        out.setdefault("search_mode", None)
        out.setdefault("last_optimize", None)
        notes = out.get("last_optimize_notes")
        if not isinstance(notes, list):
            out["last_optimize_notes"] = []
        counts = out.get("vectors_by_channel")
        if not isinstance(counts, Mapping):
            out["vectors_by_channel"] = dict(empty_counts)
        else:
            merged = dict(empty_counts)
            for k, v in counts.items():
                try:
                    merged[str(k)] = int(v or 0)
                except (TypeError, ValueError):
                    merged[str(k)] = 0
            out["vectors_by_channel"] = merged
        try:
            out["joint_repair_remaining"] = max(
                0, int(out.get("joint_repair_remaining") or 0)
            )
        except (TypeError, ValueError):
            out["joint_repair_remaining"] = 0
        try:
            out["joint_repair_last_batch"] = max(
                0, int(out.get("joint_repair_last_batch") or 0)
            )
        except (TypeError, ValueError):
            out["joint_repair_last_batch"] = 0
        return out
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "backend": "error",
            "vectors_ready": 0,
            "index_stale": False,
            "recent_buffer": 0,
            "ann_index_built": False,
            "search_mode": None,
            "last_optimize": None,
            "last_optimize_notes": [],
            "vectors_by_channel": empty_counts,
            "joint_repair_remaining": 0,
            "joint_repair_last_batch": 0,
            "error": str(exc) or type(exc).__name__,
        }


# ── Phase 2a Graph glass (PR-A5 + edges PR2) ────────────────────────────────

# Full EDGE_KINDS legend (projected + durable + ephemeral). Order is display.
_EDGE_KIND_LEGEND: tuple[dict[str, Any], ...] = (
    {
        "kind": EDGE_SEQUENTIAL,
        "base_weight": BASE_SEQUENTIAL,
        "label": "Sequential prev/next",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_PARENT_OF,
        "base_weight": BASE_PARENT_CHILD,
        "label": "Parent → child (parcel)",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_CHILD_OF,
        "base_weight": BASE_PARENT_CHILD,
        "label": "Child → parent",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_SAME_MOMENT,
        "base_weight": BASE_SAME_MOMENT,
        "label": "Same moment (soft, capped)",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_SUMMARY_CHILD,
        "base_weight": BASE_SUMMARY_CHILD,
        "label": "Summary → child summary (ladder)",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_SUMMARY_SOURCE,
        "base_weight": BASE_SUMMARY_SOURCE,
        "label": "1h summary → raw sources",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_SUPERSEDES,
        "base_weight": BASE_SUPERSEDES,
        "label": "Summary tip → previous version",
        "structural": True,
        "durable": False,
    },
    {
        "kind": EDGE_CREATED_WITH,
        "base_weight": BASE_CREATED_WITH,
        "label": "Created with (meal context)",
        "structural": False,
        "durable": True,
    },
    {
        "kind": EDGE_RECALLS,
        "base_weight": BASE_RECALLS,
        "label": "Recalls (speak-time memory)",
        "structural": False,
        "durable": True,
    },
    {
        "kind": EDGE_IN_MOMENT,
        "base_weight": BASE_IN_MOMENT,
        "label": "In moment (membership; expand rewrites peers)",
        "structural": True,
        "durable": True,
    },
    {
        "kind": EDGE_HAS_CHANNEL,
        "base_weight": BASE_HAS_CHANNEL,
        "label": "Has channel (modality; default expand omits)",
        "structural": True,
        "durable": True,
    },
    {
        "kind": EDGE_SEMANTIC_HOP,
        "base_weight": BASE_SEMANTIC_HOP,
        "label": "Semantic hop (ephemeral ANN)",
        "structural": False,
        "durable": False,
    },
)


def edge_kind_legend() -> list[dict[str, Any]]:
    """Full edge-kind legend for Graph overview covering all EDGE_KINDS.

    Includes projected sequential/parent/child/same_moment, summary_*, durable
    created_with/recalls/in_moment/has_channel, and ephemeral semantic_hop.
    """
    rows = [dict(row) for row in _EDGE_KIND_LEGEND]
    # Honesty: if a new EDGE_KINDS entry is missing from the static table,
    # surface it with base_weight so glass never silently stubs.
    known = {str(r.get("kind")) for r in rows}
    for kind in sorted(EDGE_KINDS):
        if kind in known:
            continue
        rows.append(
            {
                "kind": kind,
                "base_weight": base_weight(kind),
                "label": kind,
                "structural": False,
                "durable": kind
                in {
                    EDGE_CREATED_WITH,
                    EDGE_RECALLS,
                    EDGE_IN_MOMENT,
                    EDGE_HAS_CHANNEL,
                },
            }
        )
    return rows


def idle_age_seconds(
    updated_at: str | None,
    *,
    now: str | None = None,
) -> float | None:
    """Seconds since ``updated_at`` (idle TTL basis). None if unparseable.

    Graph session card shows idle age — not multi-hop wall-clock (KD-A18).
    """
    if not updated_at:
        return None
    try:
        then = parse_iso_z(str(updated_at))
        now_dt = parse_iso_z(now) if now else parse_iso_z(utc_now_iso())
    except (TypeError, ValueError):
        return None
    return max(0.0, (now_dt - then).total_seconds())


def graph_edge_to_inspect(
    edge: Any,
    store: MemoryStore | None = None,
    *,
    snippet_chars: int = _SNIPPET_CHARS,
) -> dict[str, Any]:
    """Serialize a GraphEdge (+ optional dst atom snippet) for glass neighbors.

    No raw embedding dumps. Snippets are truncated like Vectors neighbors.
    """
    dst_id = str(getattr(edge, "dst_atom_id", "") or "")
    src_id = str(getattr(edge, "src_atom_id", "") or "")
    kind = getattr(edge, "edge_kind", None)
    weight = getattr(edge, "weight", None)
    try:
        weight_f = float(weight) if weight is not None else None
    except (TypeError, ValueError):
        weight_f = None
    reason = str(getattr(edge, "reason", "") or "")
    meta = getattr(edge, "meta", None)
    slim_meta: dict[str, Any] = {}
    if isinstance(meta, Mapping):
        for key in (
            "moment_id",
            "parent_atom_id",
            "parcel_index",
            "cosine",
            "channel",
            "retarget_from",
            "hub",
            "membership_source",
            "direction",
        ):
            if key in meta:
                slim_meta[key] = meta[key]

    row: dict[str, Any] = {
        "atom_id": dst_id,
        "src_atom_id": src_id,
        "edge_kind": kind,
        "weight": weight_f,
        "reason": reason,
        "kind": None,
        "moment_id": None,
        "t_start": None,
        "snippet": "",
        "label": "",
        "meta": slim_meta,
    }
    if store is not None and dst_id:
        try:
            atom = store.get_atom(dst_id)
        except Exception:  # noqa: BLE001
            atom = None
        if atom is not None:
            text = getattr(atom, "content_text", None) or ""
            snip = truncate_text(str(text), max_chars=snippet_chars)
            row.update(
                {
                    "kind": getattr(atom, "kind", None),
                    "moment_id": getattr(atom, "moment_id", None),
                    "t_start": getattr(atom, "t_start", None),
                    "snippet": snip,
                    "label": snip,
                    "text_chars": len(str(text)),
                }
            )
    return row


def enrich_session_for_glass(session: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy a TraversalSession ``to_view()`` dict and add glass-only fields.

    Marks considered rows that are in ``keep_ids``; attaches idle_age_s from
    ``updated_at``. Does not invent multi-hop wall-clock countdowns (KD-A18).
    """
    if not isinstance(session, dict):
        return None
    out = dict(session)
    keep_set = {str(x) for x in (out.get("keep_ids") or [])}
    considered = out.get("considered")
    if isinstance(considered, list):
        enriched: list[dict[str, Any]] = []
        for raw in considered:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            aid = str(row.get("atom_id") or "")
            row["kept"] = aid in keep_set
            # label doubles as snippet for considered list honesty
            if "snippet" not in row and row.get("label"):
                row["snippet"] = row["label"]
            enriched.append(row)
        out["considered"] = enriched
    age = idle_age_seconds(out.get("updated_at") if isinstance(out.get("updated_at"), str) else None)
    out["idle_age_s"] = age
    # Explicitly omit any wall-clock fields if present on older payloads.
    out.pop("wall_ms_remaining", None)
    out.pop("wall_clock_ms", None)
    out.pop("session_wall_ms", None)
    return out


def graph_session_view_to_inspect(view: Any) -> dict[str, Any]:
    """Serialize GraphSessionView (or duck-type) for GET …/graph/session."""
    if view is None:
        return {
            "which": "none",
            "session": None,
            "has_active": False,
            "has_last_session": False,
            "meal_keep_count": 0,
            "meal_keep_ids": [],
        }
    if hasattr(view, "to_dict"):
        raw = view.to_dict()
    elif isinstance(view, Mapping):
        raw = dict(view)
    else:
        raw = {
            "which": getattr(view, "which", "none"),
            "session": getattr(view, "session", None),
            "has_active": bool(getattr(view, "has_active", False)),
            "has_last_session": bool(getattr(view, "has_last_session", False)),
            "meal_keep_count": int(getattr(view, "meal_keep_count", 0) or 0),
            "meal_keep_ids": list(getattr(view, "meal_keep_ids", None) or []),
        }
    sess = enrich_session_for_glass(raw.get("session") if isinstance(raw, dict) else None)
    return {
        "which": raw.get("which") or "none",
        "session": sess,
        "has_active": bool(raw.get("has_active")),
        "has_last_session": bool(raw.get("has_last_session")),
        "meal_keep_count": int(raw.get("meal_keep_count") or 0),
        "meal_keep_ids": list(raw.get("meal_keep_ids") or []),
    }


def directed_traversal_flags(settings: Any | None) -> dict[str, Any]:
    """Flag block for Graph overview honesty (defaults off)."""
    from elyra.memory.config import (
        is_directed_keep_enabled,
        is_directed_traversal_enabled,
        is_durable_edges_enabled,
    )

    trav = is_directed_traversal_enabled(settings)
    keep = is_directed_keep_enabled(settings)
    durable = is_durable_edges_enabled(settings)
    return {
        "directed_traversal_enabled": trav,
        "directed_keep_enabled": keep,
        "durable_edges_enabled": durable,
        "traverse_expand_channels": bool(
            getattr(settings, "traverse_expand_channels", False)
        )
        if settings is not None
        else False,
        # Surface key budgets so glass can explain caps without a separate call.
        "traverse_expand_max_ms": int(
            getattr(settings, "traverse_expand_max_ms", 80) or 80
        )
        if settings is not None
        else 80,
        "traverse_max_steps": int(getattr(settings, "traverse_max_steps", 8) or 8)
        if settings is not None
        else 8,
        "traverse_max_nodes": int(getattr(settings, "traverse_max_nodes", 48) or 48)
        if settings is not None
        else 48,
        "traverse_max_depth": int(getattr(settings, "traverse_max_depth", 3) or 3)
        if settings is not None
        else 3,
        "traverse_session_ttl_s": int(
            getattr(settings, "traverse_session_ttl_s", 900) or 900
        )
        if settings is not None
        else 900,
    }


__all__ = [
    "atom_to_detail",
    "atom_to_list_row",
    "atom_to_vector_row",
    "directed_traversal_flags",
    "edge_kind_legend",
    "encode_worker_health_block",
    "encoder_health_block",
    "enrich_session_for_glass",
    "graph_edge_to_inspect",
    "graph_session_view_to_inspect",
    "idle_age_seconds",
    "index_health_block",
    "list_atoms_by_embedding_status",
    "list_atoms_for_glass",
    "meal_item_to_inspect",
    "meal_package_to_inspect",
    "neighbor_hit_to_inspect",
    "query_vector_for_atom",
    "resolve_neighbor_k",
    "truncate_text",
]
