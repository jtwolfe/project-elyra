"""Split oversized atom bodies into parent + parcel children (Phase 2 PR5).

Scope: pure split helpers called from promote **before** ``_truncate``.
In scope: KD21/KD23 — parcels_enabled default false; parent on experience
kind; children ``kind=parcel`` with parent/sequential links; natural
paragraph/line/hard cut boundaries.
Out of scope: ANN→parent meal mapping (PR6); media blob policy; worker
post-promote alternate path.

Promote is the **only** call site. Encode queue picks each put via store
write hooks when semantic_enabled.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from elyra.memory.config import MemorySettings
from elyra.memory.types import Atom, atom_replace, new_atom_id

# Align with MemorySettings / promote defaults when knobs are zeroed.
_DEFAULT_THRESHOLD = 8000


def parcel_threshold(settings: MemorySettings) -> int:
    """Effective max chars per parcel/parent body slice."""
    n = int(settings.parcel_threshold_chars or 0)
    if n <= 0:
        n = int(settings.atom_max_chars or 0) or _DEFAULT_THRESHOLD
    return n if n > 0 else _DEFAULT_THRESHOLD


def should_split_into_parcels(text: str, settings: MemorySettings) -> bool:
    """True when parcels_enabled and body exceeds the parcel threshold.

    KD23: default ``parcels_enabled=False`` → always False (Phase 1 path).
    """
    if not settings.parcels_enabled:
        return False
    return len(text or "") > parcel_threshold(settings)


def split_oversized_text(text: str, max_chars: int) -> list[str]:
    """Partition ``text`` into chunks each of length ``<= max_chars``.

    Boundaries (prefer earlier cut still within window): paragraph (``\\n\\n``),
    then line (``\\n``), then hard character cut. Chunks are a pure partition:
    ``\"\".join(chunks) == text`` with no silent loss of middle content.

    If ``text`` already fits (or ``max_chars <= 0``), returns ``[text]``.
    """
    if text is None:
        return [""]
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        window = remaining[:max_chars]
        cut = _best_cut(window)
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _best_cut(window: str) -> int:
    """Return cut index in ``(0, len(window)]`` preferring natural boundaries.

    Cut is exclusive end of the current chunk so separators stay with the
    preceding chunk and ``\"\".join`` reconstructs the original.
    """
    # Prefer last paragraph break fully inside the window (not at position 0).
    idx = window.rfind("\n\n")
    if idx > 0:
        return idx + 2
    idx = window.rfind("\n")
    if idx > 0:
        return idx + 1
    # Hard cut: take the full window.
    return len(window)


def make_parent_and_parcels(
    *,
    text: str,
    max_chars: int,
    kind: str,
    t_start: str,
    moment_id: str,
    media_ids: Sequence[str] = (),
    source_beat_ts: str | None = None,
    source_beat_type: str | None = None,
    base_meta: Mapping[str, Any] | None = None,
    parent_atom_id: str | None = None,
) -> tuple[Atom, list[Atom]]:
    """Build parent (original kind, first chunk) + ``kind=parcel`` children.

    Does **not** put to the store or set experience-chain prev/next. Caller
    (promote) links the parent on the experience weave and links parcels
    sequentially among themselves only.

    Parent body = first chunk (meal-readable without expanding children).
    Children cover remaining chunks; ``\"\".join([parent]+parcels)`` == text.
    """
    chunks = split_oversized_text(text, max_chars)
    if not chunks:
        chunks = [""]

    pid = parent_atom_id or new_atom_id()
    mids = tuple(str(m) for m in (media_ids or ()) if m)
    meta_base = dict(base_meta or {})

    parcel_count = max(0, len(chunks) - 1)
    parent_meta = dict(meta_base)
    if parcel_count > 0:
        parent_meta["parcel_count"] = parcel_count
        parent_meta["has_parcels"] = True
    # Parent is never marked truncated: full text lives across parent+parcels.
    parent_meta.pop("truncated", None)

    parent = Atom(
        atom_id=pid,
        t_start=t_start,
        kind=kind,
        content_text=chunks[0],
        content_ref="inline",
        moment_id=moment_id,
        media_ids=mids,
        source_beat_ts=source_beat_ts,
        source_beat_type=source_beat_type,
        meta=parent_meta,
    )

    children: list[Atom] = []
    if parcel_count == 0:
        return parent, children

    first_parcel_id: str | None = None
    for i, chunk in enumerate(chunks[1:]):
        cid = new_atom_id()
        if first_parcel_id is None:
            first_parcel_id = cid
        child_meta: dict[str, Any] = {
            "parcel_index": i,
            "parcel_count": parcel_count,
        }
        # Carry lightweight provenance; not the full experience meta.
        if source_beat_type is not None:
            child_meta["source_beat_type"] = source_beat_type
        children.append(
            Atom(
                atom_id=cid,
                t_start=t_start,
                kind="parcel",
                content_text=chunk,
                content_ref="inline",
                moment_id=moment_id,
                media_ids=(),
                parent_atom_id=pid,
                source_beat_ts=source_beat_ts,
                source_beat_type=source_beat_type,
                meta=child_meta,
            )
        )

    if first_parcel_id is not None:
        parent_meta = dict(parent.meta)
        parent_meta["first_parcel_id"] = first_parcel_id
        parent = atom_replace(parent, meta=parent_meta)

    return parent, children


def reconstruct_text(parent: Atom, parcels: Sequence[Atom]) -> str:
    """Reconstruct full body from parent + parcel children (test helper)."""
    ordered = sorted(
        parcels,
        key=lambda a: (
            int((a.meta or {}).get("parcel_index", 0)),
            a.atom_id,
        ),
    )
    return (parent.content_text or "") + "".join(
        (p.content_text or "") for p in ordered
    )


__all__ = [
    "make_parent_and_parcels",
    "parcel_threshold",
    "reconstruct_text",
    "should_split_into_parcels",
    "split_oversized_text",
]
