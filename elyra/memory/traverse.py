"""TraversalSession — model-guided multi-step memory walk (Phase 2a PR-A2).

Scope: temporary session state machine (start / step / finish / abandon),
seed union, expand/keep, template NL summary, budgets (KD-A18: idle TTL +
expand_ms + steps — no multi-hop session wall-clock), dual sticky snapshots
(KD-A9 / KD-A19), process-local TraversalRegistry for the presence worker.

Out of scope: meal directed_keep packing (PR-A3), tools/skill (PR-A4),
glass Graph tab (PR-A5).
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Sequence

from elyra.memory.config import (
    MemorySettings,
    is_directed_traversal_enabled,
)
from elyra.memory.graph import GraphView
from elyra.memory.store import MemoryStore
from elyra.memory.types import Atom, parse_iso_z, to_iso_z, utc_now_iso

_LOG = logging.getLogger(__name__)

SessionStatus = Literal["active", "confirmed", "abandoned", "timed_out"]

ERROR_TRAVERSE_DISABLED = "traverse_disabled"
ERROR_NO_ACTIVE = "no_active_session"
ERROR_SESSION_NOT_ACTIVE = "session_not_active"
ERROR_UNKNOWN_SESSION = "unknown_session"
ERROR_BUDGET = "budget_exhausted"
ERROR_NOT_CONSIDERED = "not_in_considered"

# Defaults mirrored from MemorySettings / design budgets table.
_DEFAULT_MAX_DEPTH = 3
_DEFAULT_MAX_NODES = 48
_DEFAULT_MAX_STEPS = 8
_DEFAULT_MAX_SEEDS = 8
_DEFAULT_FRONTIER_MAX = 16
_DEFAULT_EXPAND_PER_STEP = 3
_DEFAULT_KEEP_MAX = 16
_DEFAULT_EXPAND_MS = 80
_DEFAULT_LABEL_CHARS = 80
_DEFAULT_PREVIEW_CHARS = 400
_DEFAULT_INSPECT_CHARS = 800
_DEFAULT_INSPECT_MAX_IDS = 4
_DEFAULT_INSPECT_MAX_TOTAL = 2400
_DEFAULT_SCRATCHPAD = 200
_DEFAULT_TTL_S = 900
_DEFAULT_NEIGHBOR_K = 12


def _new_session_id() -> str:
    return "tr_" + uuid.uuid4().hex


def _int_cfg(cfg: Any | None, name: str, default: int) -> int:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _bool_cfg(cfg: Any | None, name: str, default: bool) -> bool:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    return bool(raw)


def _clip(text: str | None, n: int) -> str:
    if not text or n <= 0:
        return ""
    s = str(text).replace("\n", " ").strip()
    if len(s) <= n:
        return s
    if n <= 1:
        return s[:n]
    return s[: n - 1] + "…"


def _atom_body(store: MemoryStore, atom: Atom) -> str:
    """Body text for labels/previews; parcel → parent body when available."""
    text = (atom.content_text or "").strip()
    if atom.kind == "parcel" and atom.parent_atom_id:
        parent = store.get_atom(atom.parent_atom_id)
        if parent is not None and (parent.content_text or "").strip():
            return (parent.content_text or "").strip()
    return text


def _now_ms() -> float:
    return time.monotonic() * 1000.0


# ── Budget + session DTOs ───────────────────────────────────────────────────


@dataclass
class BudgetState:
    """Session budgets (KD-A18) — no multi-hop wall-clock field."""

    max_steps: int = _DEFAULT_MAX_STEPS
    max_nodes: int = _DEFAULT_MAX_NODES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_keep: int = _DEFAULT_KEEP_MAX
    expand_ms_budget: int = _DEFAULT_EXPAND_MS
    steps_spent: int = 0
    nodes_spent: int = 0  # considered count
    depth_spent: int = 0
    expand_ms_spent_last: int = 0
    expand_truncated: bool = False

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.steps_spent)

    @property
    def nodes_remaining(self) -> int:
        return max(0, self.max_nodes - self.nodes_spent)

    @property
    def depth_remaining(self) -> int:
        return max(0, self.max_depth - self.depth_spent)

    def keep_slots_remaining(self, keep_count: int) -> int:
        return max(0, self.max_keep - keep_count)

    def to_surface(self, *, keep_count: int = 0) -> dict[str, Any]:
        """Thin decision surface budget block (no session wall countdown)."""
        return {
            "nodes_remaining": self.nodes_remaining,
            "depth_remaining": self.depth_remaining,
            "steps_remaining": self.steps_remaining,
            "keep_slots_remaining": self.keep_slots_remaining(keep_count),
            "expand_ms_budget": self.expand_ms_budget,
            "expand_ms_spent_last": self.expand_ms_spent_last,
            "expand_truncated": self.expand_truncated,
            "nodes_spent": self.nodes_spent,
            "depth_spent": self.depth_spent,
            "steps_spent": self.steps_spent,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_steps": self.max_steps,
            "max_keep": self.max_keep,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_keep": self.max_keep,
            "expand_ms_budget": self.expand_ms_budget,
            "steps_spent": self.steps_spent,
            "nodes_spent": self.nodes_spent,
            "depth_spent": self.depth_spent,
            "expand_ms_spent_last": self.expand_ms_spent_last,
            "expand_truncated": self.expand_truncated,
            "steps_remaining": self.steps_remaining,
            "nodes_remaining": self.nodes_remaining,
            "depth_remaining": self.depth_remaining,
        }


@dataclass
class ConsideredNode:
    atom_id: str
    kind: str | None
    label: str
    preview: str
    via_edge_kind: str | None
    via_reason: str | None
    depth: int
    weight: float | None
    parent_id: str | None = None  # walk-tree parent (expand source)

    def summary(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "kind": self.kind,
            "label": self.label,
            "via_edge_kind": self.via_edge_kind,
            "depth": self.depth,
            "weight": self.weight,
        }


@dataclass
class FrontierItem:
    atom_id: str
    label: str
    preview: str
    kind: str | None
    edge_kind: str | None
    weight: float
    reason: str
    depth: int

    def to_surface(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "label": self.label,
            "preview": self.preview,
            "kind": self.kind,
            "edge_kind": self.edge_kind,
            "weight": self.weight,
            "reason": self.reason,
            "depth": self.depth,
        }


@dataclass
class ConfirmedKeepSnapshot:
    """Meal-only thin slice — NOT the glass last-walk source (KD-A19)."""

    session_id: str
    goal: str
    keep_ids: tuple[str, ...]
    walk_summary_nl: str
    finished_at: str
    moment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "keep_ids": list(self.keep_ids),
            "walk_summary_nl": self.walk_summary_nl,
            "finished_at": self.finished_at,
            "moment_id": self.moment_id,
        }


@dataclass
class AtomPreview:
    atom_id: str
    kind: str | None
    body: str
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "atom_id": self.atom_id,
            "kind": self.kind,
            "body": self.body,
            "truncated": self.truncated,
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class TraversalSession:
    """In-process temporary walk state (no durable Atom rows — KD-A1)."""

    session_id: str
    goal: str
    status: SessionStatus
    seed_ids: tuple[str, ...]
    considered: dict[str, ConsideredNode]
    frontier: list[FrontierItem]
    keep_ids: list[str]
    scratchpad: str
    budgets: BudgetState
    created_at: str
    updated_at: str
    finished_at: str | None = None
    moment_id: str | None = None
    walk_summary_nl: str | None = None
    seed_reasons: list[str] = field(default_factory=list)
    edge_kind_counts: dict[str, int] = field(default_factory=dict)
    expand_truncated: bool = False
    error_reason: str | None = None

    def touch(self, now: str) -> None:
        self.updated_at = now

    def to_view(self) -> dict[str, Any]:
        """Full session DTO for glass / tests (considered summary + budgets)."""
        considered_list = [
            n.summary()
            for n in sorted(
                self.considered.values(),
                key=lambda c: (c.depth, c.atom_id),
            )
        ]
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "seed_ids": list(self.seed_ids),
            "seed_reasons": list(self.seed_reasons),
            "considered": considered_list,
            "considered_count": len(self.considered),
            "frontier": [f.to_surface() for f in self.frontier],
            "keep_ids": list(self.keep_ids),
            "scratchpad": self.scratchpad,
            "walk_summary_nl": self.walk_summary_nl,
            "budgets": self.budgets.snapshot(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "moment_id": self.moment_id,
            "expand_truncated": self.expand_truncated,
            "edge_kind_counts": dict(self.edge_kind_counts),
            "error_reason": self.error_reason,
        }

    def to_thin_surface(self) -> dict[str, Any]:
        """Model-facing thin decision surface (no full considered dump)."""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "goal": self.goal,
            "budget": self.budgets.to_surface(keep_count=len(self.keep_ids)),
            "frontier": [f.to_surface() for f in self.frontier],
            "keep_set": list(self.keep_ids),
            "scratchpad": self.scratchpad,
            "considered_count": len(self.considered),
            "seed_reasons": list(self.seed_reasons),
            "expand_truncated": self.expand_truncated,
            "walk_summary_nl": self.walk_summary_nl,
            "error_reason": self.error_reason,
        }


@dataclass
class GraphSessionView:
    """Prefer active if present else last_session (glass GET contract)."""

    which: Literal["active", "last", "none"]
    session: dict[str, Any] | None
    has_active: bool
    has_last_session: bool
    meal_keep_count: int
    meal_keep_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "which": self.which,
            "session": self.session,
            "has_active": self.has_active,
            "has_last_session": self.has_last_session,
            "meal_keep_count": self.meal_keep_count,
            "meal_keep_ids": list(self.meal_keep_ids),
        }


# ── Pure helpers ────────────────────────────────────────────────────────────


def build_walk_summary_nl(
    session: TraversalSession,
    *,
    summary_hint: str | None = None,
    goal_max: int = 80,
    labels_max: int = 6,
) -> str:
    """Deterministic template NL walk summary (KD-A6)."""
    goal_short = _clip(session.goal, goal_max) or "memories"
    n_considered = len(session.considered)
    n_steps = session.budgets.steps_spent
    seed_kinds = ", ".join(session.seed_reasons) if session.seed_reasons else "none"
    hist = session.edge_kind_counts
    if hist:
        edges = ", ".join(f"{k}={v}" for k, v in sorted(hist.items()))
    else:
        edges = "none"
    n_kept = len(session.keep_ids)
    labels: list[str] = []
    for kid in session.keep_ids[:labels_max]:
        node = session.considered.get(kid)
        if node and node.label:
            labels.append(node.label)
        else:
            labels.append(kid[:12])
    kept_part = ", ".join(labels) if labels else "none"
    if n_kept > labels_max:
        kept_part += f" (+{n_kept - labels_max} more)"
    lines = [
        f"I walked through memories about {goal_short}.",
        f"Considered {n_considered} atoms across {n_steps} steps "
        f"(seeds: {seed_kinds}; edges: {edges}).",
        f"Kept {n_kept}: {kept_part}.",
    ]
    hint = (summary_hint or "").strip()
    if hint:
        lines.append(_clip(hint, 240))
    return "\n".join(lines)


def inspect_atoms(
    store: MemoryStore,
    atom_ids: Sequence[str],
    *,
    settings: MemorySettings | None = None,
    chars_per_id: int | None = None,
    max_ids: int | None = None,
    max_total_chars: int | None = None,
) -> list[AtomPreview]:
    """Return capped body slices for mid-walk inspect (KD-A17)."""
    cfg = settings or MemorySettings()
    per = (
        chars_per_id
        if chars_per_id is not None
        else _int_cfg(cfg, "traverse_inspect_chars_per_id", _DEFAULT_INSPECT_CHARS)
    )
    mid = (
        max_ids
        if max_ids is not None
        else _int_cfg(cfg, "traverse_inspect_max_ids", _DEFAULT_INSPECT_MAX_IDS)
    )
    total_cap = (
        max_total_chars
        if max_total_chars is not None
        else _int_cfg(
            cfg, "traverse_inspect_max_total_chars", _DEFAULT_INSPECT_MAX_TOTAL
        )
    )
    mid = max(0, mid)
    per = max(0, per)
    total_cap = max(0, total_cap)
    out: list[AtomPreview] = []
    used = 0
    for raw in list(atom_ids)[:mid]:
        aid = str(raw)
        atom = store.get_atom(aid)
        if atom is None:
            out.append(
                AtomPreview(
                    atom_id=aid,
                    kind=None,
                    body="",
                    error="atom_not_found",
                )
            )
            continue
        body = _atom_body(store, atom)
        room = max(0, total_cap - used)
        take = min(per, room)
        truncated = len(body) > take
        slice_ = body[:take] if take > 0 else ""
        used += len(slice_)
        out.append(
            AtomPreview(
                atom_id=aid,
                kind=atom.kind,
                body=slice_,
                truncated=truncated,
            )
        )
        if used >= total_cap:
            break
    return out


# ── TraversalRegistry (worker-owned) ────────────────────────────────────────


class TraversalRegistry:
    """Process-local session registry: active + last_session + last_confirmed_keep.

    Single active session (one open moment at a time in the worker). Sticky
    ``last_session`` (glass KD-A19) and ``last_confirmed_keep`` (meal-thin)
    survive abandon / idle TTL / new start; moment-close clears both.
    """

    def __init__(
        self,
        *,
        settings: MemorySettings | None = None,
        now_fn: Callable[[], str] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings or MemorySettings()
        self._now_fn = now_fn or utc_now_iso
        self._monotonic = monotonic_fn or (lambda: time.monotonic())
        self._active: TraversalSession | None = None
        self._last_session: TraversalSession | None = None
        self._last_confirmed_keep: ConfirmedKeepSnapshot | None = None

    # -- settings / factories ------------------------------------------------

    def bind_settings(self, settings: MemorySettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def active_session(self) -> TraversalSession | None:
        return self._active

    @property
    def last_session(self) -> TraversalSession | None:
        return self._last_session

    @property
    def last_confirmed_keep(self) -> ConfirmedKeepSnapshot | None:
        return self._last_confirmed_keep

    def enabled(self) -> bool:
        return is_directed_traversal_enabled(self._settings)

    # -- queries -------------------------------------------------------------

    def get_traversal(self, session_id: str) -> TraversalSession | None:
        if self._active is not None and self._active.session_id == session_id:
            return self._active
        if (
            self._last_session is not None
            and self._last_session.session_id == session_id
        ):
            return self._last_session
        return None

    def get_last_confirmed_keep(
        self, moment_id: str | None = None
    ) -> ConfirmedKeepSnapshot | None:
        snap = self._last_confirmed_keep
        if snap is None:
            return None
        if moment_id is not None and snap.moment_id not in (None, moment_id):
            return None
        return snap

    def get_last_session(
        self, moment_id: str | None = None
    ) -> TraversalSession | None:
        sess = self._last_session
        if sess is None:
            return None
        if moment_id is not None and sess.moment_id not in (None, moment_id):
            return None
        return sess

    def get_graph_session_view(
        self, moment_id: str | None = None, *, which: str | None = None
    ) -> GraphSessionView:
        """Prefer active if present else last_session (glass GET)."""
        meal = self.get_last_confirmed_keep(moment_id)
        meal_ids = list(meal.keep_ids) if meal else []
        active = self._active
        last = self.get_last_session(moment_id)
        has_active = active is not None
        has_last = last is not None

        want = (which or "").strip().lower()
        if want == "meal":
            return GraphSessionView(
                which="none",
                session=None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if want == "last":
            sess = last
            return GraphSessionView(
                which="last" if sess else "none",
                session=sess.to_view() if sess else None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if want == "active":
            sess = active
            return GraphSessionView(
                which="active" if sess else "none",
                session=sess.to_view() if sess else None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        # Default: active else last.
        if active is not None:
            return GraphSessionView(
                which="active",
                session=active.to_view(),
                has_active=True,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if last is not None:
            return GraphSessionView(
                which="last",
                session=last.to_view(),
                has_active=False,
                has_last_session=True,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        return GraphSessionView(
            which="none",
            session=None,
            has_active=False,
            has_last_session=False,
            meal_keep_count=len(meal_ids),
            meal_keep_ids=meal_ids,
        )

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        graph: GraphView,
        *,
        goal: str,
        seed_query: str | None = None,
        seed_atom_ids: Sequence[str] | None = None,
        moment_id: str | None = None,
        budget_overrides: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Create an active session; abandon any previous active only.

        Retains ``last_confirmed_keep`` and ``last_session`` (KD-A9 / KD-A19).
        Flags-off → ``error_reason=traverse_disabled`` without mutating sticky.
        """
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }

        now = self._now_fn()
        # Abandon previous active only — sticky snapshots stay.
        if self._active is not None and self._active.status == "active":
            self._drop_active(status="abandoned", now=now)

        cfg = self._settings
        label_n = _int_cfg(cfg, "traverse_label_chars", _DEFAULT_LABEL_CHARS)
        preview_n = _int_cfg(cfg, "traverse_preview_chars", _DEFAULT_PREVIEW_CHARS)
        max_seeds = _int_cfg(cfg, "traverse_max_seeds", _DEFAULT_MAX_SEEDS)
        max_nodes = _int_cfg(cfg, "traverse_max_nodes", _DEFAULT_MAX_NODES)
        max_depth = _int_cfg(cfg, "traverse_max_depth", _DEFAULT_MAX_DEPTH)
        max_steps = _int_cfg(cfg, "traverse_max_steps", _DEFAULT_MAX_STEPS)
        max_keep = _int_cfg(cfg, "traverse_keep_max", _DEFAULT_KEEP_MAX)
        expand_ms = _int_cfg(cfg, "traverse_expand_max_ms", _DEFAULT_EXPAND_MS)
        start_ms = _int_cfg(cfg, "traverse_start_expand_max_ms", 0)
        if start_ms <= 0:
            start_ms = expand_ms
        frontier_max = _int_cfg(cfg, "traverse_frontier_max", _DEFAULT_FRONTIER_MAX)

        if budget_overrides:
            max_steps = min(max_steps, max(0, int(budget_overrides.get("max_steps", max_steps))))
            max_nodes = min(max_nodes, max(0, int(budget_overrides.get("max_nodes", max_nodes))))
            max_depth = min(max_depth, max(0, int(budget_overrides.get("max_depth", max_depth))))
            max_keep = min(max_keep, max(0, int(budget_overrides.get("max_keep", max_keep))))

        budgets = BudgetState(
            max_steps=max_steps,
            max_nodes=max_nodes,
            max_depth=max_depth,
            max_keep=max_keep,
            expand_ms_budget=expand_ms,
        )

        session = TraversalSession(
            session_id=_new_session_id(),
            goal=(goal or "").strip() or "explore",
            status="active",
            seed_ids=(),
            considered={},
            frontier=[],
            keep_ids=[],
            scratchpad="",
            budgets=budgets,
            created_at=now,
            updated_at=now,
            moment_id=moment_id,
        )

        store = graph._store  # noqa: SLF001 — same process GraphView
        seed_order: list[str] = []
        seed_reason_tags: list[str] = []
        expand_truncated = False
        expand_ms_spent = 0

        # 1) Explicit seeds (point lookups — free of expand_ms).
        for raw in seed_atom_ids or ():
            if len(seed_order) >= max_seeds:
                break
            aid = str(raw)
            if aid in session.considered:
                continue
            atom = store.get_atom(aid)
            if atom is None:
                continue
            self._add_considered(
                session,
                store,
                atom,
                via_edge_kind=None,
                via_reason="seed:explicit",
                depth=0,
                weight=1.0,
                parent_id=None,
                label_n=label_n,
                preview_n=preview_n,
            )
            seed_order.append(aid)
            if "explicit" not in seed_reason_tags:
                seed_reason_tags.append("explicit")

        # 2) Semantic seed_from_text under start expand_ms.
        q = (seed_query if seed_query is not None else session.goal).strip()
        if q and len(seed_order) < max_seeds:
            t0 = _now_ms()
            room = max_seeds - len(seed_order)
            hits = graph.seed_from_text(
                q,
                k=room,
                exclude_moment_id=moment_id,
                expand_deadline_ms=start_ms,
            )
            expand_ms_spent = int(_now_ms() - t0)
            meta = graph.last_expand_meta
            if meta.get("expand_truncated"):
                expand_truncated = True
            sem_reason = meta.get("semantic_reason")
            if hits:
                if "semantic" not in seed_reason_tags:
                    seed_reason_tags.append("semantic")
            elif sem_reason:
                # Surface empty reasons without failing start.
                tag = str(sem_reason)
                if tag not in seed_reason_tags:
                    seed_reason_tags.append(tag)
            if expand_truncated and "expand_truncated" not in seed_reason_tags:
                seed_reason_tags.append("expand_truncated")
            for aid, score, reason in hits:
                if len(seed_order) >= max_seeds:
                    break
                if aid in session.considered:
                    continue
                atom = store.get_atom(aid)
                if atom is None:
                    continue
                self._add_considered(
                    session,
                    store,
                    atom,
                    via_edge_kind="semantic_hop",
                    via_reason=reason,
                    depth=0,
                    weight=float(score),
                    parent_id=None,
                    label_n=label_n,
                    preview_n=preview_n,
                )
                seed_order.append(aid)

        # 3) Temporal seeds free (not under expand_ms).
        if len(seed_order) < max_seeds:
            around = seed_order[0] if seed_order else None
            if around is None:
                # Open-moment tail / global tail anchor.
                if moment_id:
                    tail = store.moment_tail(moment_id)
                else:
                    tail = store.global_tail()
                around = tail.atom_id if tail is not None else None
            room = max_seeds - len(seed_order)
            temporal = graph.seed_temporal(
                around_atom_id=around,
                moment_id=moment_id,
                k=room + len(seed_order),  # room after exclude already-seen
            )
            added_temporal = False
            for aid, score, reason in temporal:
                if len(seed_order) >= max_seeds:
                    break
                if aid in session.considered:
                    continue
                atom = store.get_atom(aid)
                if atom is None:
                    continue
                self._add_considered(
                    session,
                    store,
                    atom,
                    via_edge_kind=None,
                    via_reason=reason,
                    depth=0,
                    weight=float(score),
                    parent_id=None,
                    label_n=label_n,
                    preview_n=preview_n,
                )
                seed_order.append(aid)
                added_temporal = True
            if added_temporal and "temporal" not in seed_reason_tags:
                seed_reason_tags.append("temporal")

        session.seed_ids = tuple(seed_order)
        session.seed_reasons = seed_reason_tags
        session.expand_truncated = expand_truncated
        session.budgets.expand_ms_spent_last = expand_ms_spent
        session.budgets.expand_truncated = expand_truncated
        session.budgets.nodes_spent = len(session.considered)
        session.budgets.depth_spent = 0

        # Frontier = seeds ranked by weight.
        self._rebuild_frontier(session, frontier_max=frontier_max)
        self._active = session

        view = session.to_thin_surface()
        view["ok"] = True
        view["seed_ids"] = list(session.seed_ids)
        return view

    def step(
        self,
        graph: GraphView,
        *,
        session_id: str | None = None,
        expand_ids: Sequence[str] | None = None,
        keep_ids: Sequence[str] | None = None,
        scratchpad: str | None = None,
    ) -> dict[str, Any]:
        """One tool step: expand selected frontier nodes + optional keep."""
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        session.touch(now)
        cfg = self._settings
        label_n = _int_cfg(cfg, "traverse_label_chars", _DEFAULT_LABEL_CHARS)
        preview_n = _int_cfg(cfg, "traverse_preview_chars", _DEFAULT_PREVIEW_CHARS)
        expand_per = _int_cfg(
            cfg, "traverse_max_expand_per_step", _DEFAULT_EXPAND_PER_STEP
        )
        frontier_max = _int_cfg(cfg, "traverse_frontier_max", _DEFAULT_FRONTIER_MAX)
        expand_ms = _int_cfg(cfg, "traverse_expand_max_ms", _DEFAULT_EXPAND_MS)
        scratch_n = _int_cfg(cfg, "traverse_scratchpad_chars", _DEFAULT_SCRATCHPAD)

        if scratchpad is not None:
            session.scratchpad = _clip(scratchpad, scratch_n)

        # Provisional keeps (must be considered).
        if keep_ids:
            self._merge_keeps(session, keep_ids)

        # Budget gate: no expand if steps/nodes/depth exhausted.
        can_expand = (
            session.budgets.steps_remaining > 0
            and session.budgets.nodes_remaining > 0
            and session.budgets.depth_remaining > 0
        )
        store = graph._store  # noqa: SLF001
        expand_truncated = False
        expand_ms_spent = 0
        newly: list[str] = []

        if can_expand and expand_ids:
            picks = [str(x) for x in expand_ids][: max(0, expand_per)]
            t0 = _now_ms()
            deadline = float(expand_ms) if expand_ms > 0 else None
            session.budgets.steps_spent += 1

            for src_id in picks:
                if session.budgets.nodes_remaining <= 0:
                    break
                if deadline is not None and (_now_ms() - t0) > deadline:
                    expand_truncated = True
                    break
                src_node = session.considered.get(src_id)
                if src_node is None:
                    continue
                # Depth of destinations = parent depth + 1.
                next_depth = src_node.depth + 1
                if next_depth > session.budgets.max_depth:
                    continue
                remaining_ms = None
                if deadline is not None:
                    remaining_ms = max(0, int(deadline - (_now_ms() - t0)))
                    if remaining_ms <= 0:
                        expand_truncated = True
                        break
                edges = graph.neighbors(
                    src_id,
                    k=_DEFAULT_NEIGHBOR_K,
                    exclude_ids=set(session.considered.keys()),
                    expand_deadline_ms=remaining_ms,
                )
                meta = graph.last_expand_meta
                if meta.get("expand_truncated"):
                    expand_truncated = True
                for e in edges:
                    if session.budgets.nodes_remaining <= 0:
                        break
                    if deadline is not None and (_now_ms() - t0) > deadline:
                        expand_truncated = True
                        break
                    if e.dst_atom_id in session.considered:
                        continue
                    atom = store.get_atom(e.dst_atom_id)
                    if atom is None:
                        continue
                    self._add_considered(
                        session,
                        store,
                        atom,
                        via_edge_kind=e.edge_kind,
                        via_reason=e.reason,
                        depth=next_depth,
                        weight=e.weight,
                        parent_id=src_id,
                        label_n=label_n,
                        preview_n=preview_n,
                    )
                    newly.append(e.dst_atom_id)
                    session.edge_kind_counts[e.edge_kind] = (
                        session.edge_kind_counts.get(e.edge_kind, 0) + 1
                    )
                    # Update depth_spent to max path hops from seeds.
                    if next_depth > session.budgets.depth_spent:
                        session.budgets.depth_spent = next_depth
            expand_ms_spent = int(_now_ms() - t0)
        elif not can_expand and expand_ids:
            # Still count a step attempt? Design: exceed caps → stop further
            # expand; finish with partial keep still allowed. Do not increment
            # steps when we refuse expand due to budget (surface remaining 0).
            pass

        session.budgets.nodes_spent = len(session.considered)
        session.budgets.expand_ms_spent_last = expand_ms_spent
        session.budgets.expand_truncated = expand_truncated
        session.expand_truncated = expand_truncated or session.expand_truncated

        self._rebuild_frontier(
            session, frontier_max=frontier_max, prefer_new=newly
        )

        view = session.to_thin_surface()
        view["ok"] = True
        view["newly_expanded"] = newly
        return view

    def finish(
        self,
        graph: GraphView | None = None,
        *,
        session_id: str | None = None,
        keep_ids: Sequence[str] | None = None,
        summary_hint: str | None = None,
    ) -> dict[str, Any]:
        """Confirm active session → last_session + last_confirmed_keep."""
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        session.touch(now)
        if keep_ids is not None:
            # Replace keep-set with final list (still must be considered).
            session.keep_ids = []
            self._merge_keeps(session, keep_ids)

        # Optional sequential ±1 adjacent expand of keep set.
        if (
            graph is not None
            and _bool_cfg(self._settings, "traverse_keep_adjacent", True)
        ):
            self._keep_adjacent(session, graph)

        session.status = "confirmed"
        session.finished_at = now
        session.walk_summary_nl = build_walk_summary_nl(
            session, summary_hint=summary_hint
        )
        session.frontier = []  # freeze / empty frontier on confirm

        # Dual snapshot (KD-A9 + KD-A19).
        frozen = copy.deepcopy(session)
        self._last_session = frozen
        self._last_confirmed_keep = ConfirmedKeepSnapshot(
            session_id=session.session_id,
            goal=session.goal,
            keep_ids=tuple(session.keep_ids),
            walk_summary_nl=session.walk_summary_nl or "",
            finished_at=now,
            moment_id=session.moment_id,
        )
        self._active = None

        view = frozen.to_view()
        view["ok"] = True
        view["keep_set"] = list(frozen.keep_ids)
        view["thin_surface"] = frozen.to_thin_surface()
        return view

    def abandon(
        self,
        *,
        session_id: str | None = None,
        reason: str = "abandoned",
    ) -> dict[str, Any]:
        """Abandon active only — sticky last_session + last_confirmed retained."""
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        status: SessionStatus = (
            "timed_out" if reason in ("timed_out", "idle_ttl", "timeout") else "abandoned"
        )
        dropped = self._drop_active(status=status, now=now)
        view = dropped.to_view() if dropped else {"status": status}
        view["ok"] = True
        view["last_session_retained"] = self._last_session is not None
        view["last_confirmed_retained"] = self._last_confirmed_keep is not None
        return view

    def clear_confirmed_keep(
        self,
        *,
        moment_id: str | None = None,
        clear_glass: bool = False,
    ) -> dict[str, Any]:
        """Clear meal thin snapshot; optional clear_glass drops last_session."""
        snap = self._last_confirmed_keep
        if moment_id is not None and snap is not None:
            if snap.moment_id not in (None, moment_id):
                return {"ok": True, "cleared_keep": False, "cleared_glass": False}
        self._last_confirmed_keep = None
        cleared_glass = False
        if clear_glass:
            if moment_id is None or (
                self._last_session is not None
                and self._last_session.moment_id in (None, moment_id)
            ):
                self._last_session = None
                cleared_glass = True
        return {
            "ok": True,
            "cleared_keep": True,
            "cleared_glass": cleared_glass,
        }

    def sweep_idle(self, *, now: str | None = None) -> TraversalSession | None:
        """Abandon active if idle longer than traverse_session_ttl_s.

        Does not touch last_confirmed_keep or last_session.
        Returns the dropped session if any.
        """
        active = self._active
        if active is None or active.status != "active":
            return None
        ttl = _int_cfg(self._settings, "traverse_session_ttl_s", _DEFAULT_TTL_S)
        if ttl <= 0:
            return None
        try:
            updated = parse_iso_z(active.updated_at)
            now_dt = parse_iso_z(now or self._now_fn())
        except (TypeError, ValueError):
            return None
        age_s = (now_dt - updated).total_seconds()
        if age_s < ttl:
            return None
        return self._drop_active(
            status="timed_out", now=to_iso_z(now_dt) if now is None else (now or self._now_fn())
        )

    def on_moment_close(self, moment_id: str | None = None) -> None:
        """Moment end: abandon active; clear last_confirmed_keep AND last_session."""
        now = self._now_fn()
        if self._active is not None:
            if moment_id is None or self._active.moment_id in (None, moment_id):
                self._drop_active(status="abandoned", now=now)
        if self._last_confirmed_keep is not None:
            if moment_id is None or self._last_confirmed_keep.moment_id in (
                None,
                moment_id,
            ):
                self._last_confirmed_keep = None
        if self._last_session is not None:
            if moment_id is None or self._last_session.moment_id in (None, moment_id):
                self._last_session = None

    def reset(self) -> None:
        """Full process reset (runtime wipe)."""
        self._active = None
        self._last_session = None
        self._last_confirmed_keep = None

    # -- internals -----------------------------------------------------------

    def _require_active(
        self, session_id: str | None
    ) -> TraversalSession | dict[str, Any]:
        active = self._active
        if active is None or active.status != "active":
            return {
                "ok": False,
                "error_reason": ERROR_NO_ACTIVE,
                "status": "none",
            }
        if session_id is not None and active.session_id != session_id:
            return {
                "ok": False,
                "error_reason": ERROR_UNKNOWN_SESSION,
                "status": active.status,
                "session_id": active.session_id,
            }
        return active

    def _drop_active(
        self, *, status: SessionStatus, now: str
    ) -> TraversalSession | None:
        active = self._active
        if active is None:
            return None
        active.status = status
        active.finished_at = now
        active.updated_at = now
        active.keep_ids = []  # provisional keep discarded
        active.walk_summary_nl = None
        self._active = None
        # v1: do not promote abandoned to last_session (design: optional).
        return active

    def _add_considered(
        self,
        session: TraversalSession,
        store: MemoryStore,
        atom: Atom,
        *,
        via_edge_kind: str | None,
        via_reason: str | None,
        depth: int,
        weight: float | None,
        parent_id: str | None,
        label_n: int,
        preview_n: int,
    ) -> None:
        if atom.atom_id in session.considered:
            return
        if len(session.considered) >= session.budgets.max_nodes:
            return
        body = _atom_body(store, atom)
        node = ConsideredNode(
            atom_id=atom.atom_id,
            kind=atom.kind,
            label=_clip(body, label_n),
            preview=_clip(body, preview_n),
            via_edge_kind=via_edge_kind,
            via_reason=via_reason,
            depth=depth,
            weight=weight,
            parent_id=parent_id,
        )
        session.considered[atom.atom_id] = node

    def _merge_keeps(
        self, session: TraversalSession, keep_ids: Sequence[str]
    ) -> None:
        max_keep = session.budgets.max_keep
        for raw in keep_ids:
            aid = str(raw)
            if aid in session.keep_ids:
                continue
            if aid not in session.considered:
                continue
            if len(session.keep_ids) >= max_keep:
                break
            session.keep_ids.append(aid)

    def _keep_adjacent(self, session: TraversalSession, graph: GraphView) -> None:
        """Expand keep-set with sequential ±1 neighbours still under keep_max."""
        store = graph._store  # noqa: SLF001
        max_keep = session.budgets.max_keep
        extras: list[str] = []
        for kid in list(session.keep_ids):
            atom = store.get_atom(kid)
            if atom is None:
                continue
            for peer_id in (atom.prev_atom_id, atom.next_atom_id):
                if not peer_id or peer_id in session.keep_ids or peer_id in extras:
                    continue
                peer = store.get_atom(peer_id)
                if peer is None:
                    continue
                # Ensure considered for summary labels.
                if peer_id not in session.considered:
                    if len(session.considered) < session.budgets.max_nodes:
                        label_n = _int_cfg(
                            self._settings,
                            "traverse_label_chars",
                            _DEFAULT_LABEL_CHARS,
                        )
                        preview_n = _int_cfg(
                            self._settings,
                            "traverse_preview_chars",
                            _DEFAULT_PREVIEW_CHARS,
                        )
                        parent_node = session.considered.get(kid)
                        self._add_considered(
                            session,
                            store,
                            peer,
                            via_edge_kind="sequential",
                            via_reason="keep_adjacent",
                            depth=parent_node.depth if parent_node else 0,
                            weight=None,
                            parent_id=kid,
                            label_n=label_n,
                            preview_n=preview_n,
                        )
                extras.append(peer_id)
        for aid in extras:
            if len(session.keep_ids) >= max_keep:
                break
            if aid not in session.keep_ids:
                session.keep_ids.append(aid)
        session.budgets.nodes_spent = len(session.considered)

    def _rebuild_frontier(
        self,
        session: TraversalSession,
        *,
        frontier_max: int,
        prefer_new: Sequence[str] | None = None,
    ) -> None:
        """Rebuild frontier from considered nodes ranked by weight (drop lowest)."""
        prefer = set(prefer_new or ())
        items: list[FrontierItem] = []
        for node in session.considered.values():
            # Seeds + newly expanded stay on frontier; already-kept can remain.
            w = float(node.weight) if node.weight is not None else 0.0
            items.append(
                FrontierItem(
                    atom_id=node.atom_id,
                    label=node.label,
                    preview=node.preview if node.atom_id in prefer or node.depth == 0 else "",
                    kind=node.kind,
                    edge_kind=node.via_edge_kind,
                    weight=w,
                    reason=node.via_reason or "seed",
                    depth=node.depth,
                )
            )
        # Prefer newly expanded for preview fill; rank weight desc.
        items.sort(
            key=lambda f: (
                0 if f.atom_id in prefer else 1,
                -f.weight,
                f.depth,
                f.atom_id,
            )
        )
        # Cap; drop lowest weight (already sorted).
        cap = max(0, frontier_max)
        session.frontier = items[:cap]
        # Ensure previews on frontier for seeds / new (design KD-A17).
        for f in session.frontier:
            node = session.considered.get(f.atom_id)
            if node and not f.preview:
                f.preview = node.preview


__all__ = [
    "ERROR_BUDGET",
    "ERROR_NO_ACTIVE",
    "ERROR_NOT_CONSIDERED",
    "ERROR_SESSION_NOT_ACTIVE",
    "ERROR_TRAVERSE_DISABLED",
    "ERROR_UNKNOWN_SESSION",
    "AtomPreview",
    "BudgetState",
    "ConfirmedKeepSnapshot",
    "ConsideredNode",
    "FrontierItem",
    "GraphSessionView",
    "TraversalRegistry",
    "TraversalSession",
    "build_walk_summary_nl",
    "inspect_atoms",
]
