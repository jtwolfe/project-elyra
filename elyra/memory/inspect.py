"""Read-only memory inspection helpers for glass /api/memory/* (PR9 + Phase 2 PR7).

Scope: serialize meal packages, atoms, and vector/neighbor inspect for operator UI.
Out of scope: atom edit/delete, raw 2048-d dumps, graph product.
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
    to_iso_z,
    utc_now_iso,
)

# Truncation for glass list rows (not store limits).
_SNIPPET_CHARS = 240
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


def encoder_health_block(
    *,
    settings: Any | None,
    embedder: Any | None,
    queue: Any | None,
) -> dict[str, Any]:
    """JSON-ready encoder + queue health for Vectors overview (no secrets)."""
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
        "queue_depth": 0,
        "queue_max": int(getattr(cfg, "encode_queue_max", 1024) or 1024)
        if cfg
        else 1024,
        "queue_dropped": 0,
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

    if embedder is None:
        if not embed_enabled:
            block["error"] = "embed_disabled"
        else:
            block["error"] = "encoder_not_loaded"
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
    except Exception as exc:  # noqa: BLE001
        block["ok"] = False
        block["error"] = str(exc) or type(exc).__name__
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


__all__ = [
    "atom_to_detail",
    "atom_to_list_row",
    "atom_to_vector_row",
    "encoder_health_block",
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
