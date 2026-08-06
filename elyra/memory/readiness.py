"""Memory fabric readiness: component gates + aggregate ``memory_ready``.

Design: ``docs/design/design-warm-on-start.md`` §2 (KD-GATE / KD-MR).

``memory_ready`` is operator/Glass fabric-OK only. Consumers must gate on
component flags (``edges_ready``, ``embedder_ready``, …), not the aggregate.

``chat_ready`` is independent and never computed here.
"""

from __future__ import annotations

from typing import Any, Mapping


def need_store(*, enabled: bool, write_atoms: bool) -> bool:
    """Atom store is required when memory is enabled or write_atoms is on."""
    return bool(enabled) or bool(write_atoms)


def need_index(*, enabled: bool, write_atoms: bool) -> bool:
    """Index opens with store; NullEmbeddingIndex is OK for jsonl."""
    return need_store(enabled=enabled, write_atoms=write_atoms)


def need_edges(
    *,
    enabled: bool,
    write_atoms: bool,
    backend: str,
    durable_edges_enabled: bool,
) -> bool:
    """Edges required for aggregate when store on and (lance or durable writes).

    Truth table (design §2.3):
    - memory off → not required
    - jsonl + durable_edges=false → not required
    - jsonl + durable_edges=true → required
    - lance (any durable flag) → required (read-open even when writes off)
    """
    if not need_store(enabled=enabled, write_atoms=write_atoms):
        return False
    backend_norm = str(backend or "").strip().lower()
    if backend_norm == "lance":
        return True
    return bool(durable_edges_enabled)


def need_embed(
    *,
    enabled: bool,
    write_atoms: bool,
    embed_enabled: bool,
) -> bool:
    """Embedder required for aggregate when store on and embed_enabled."""
    if not need_store(enabled=enabled, write_atoms=write_atoms):
        return False
    return bool(embed_enabled)


def compute_memory_ready(
    *,
    enabled: bool,
    write_atoms: bool,
    backend: str,
    durable_edges_enabled: bool,
    embed_enabled: bool,
    store_open: bool,
    store_ok: bool,
    index_ready: bool,
    edges_ready: bool,
    embedder_ready: bool,
) -> dict[str, Any]:
    """Compute aggregate ``memory_ready`` and required-component mask.

    Returns a dict suitable for status / tests::

        {
          "memory_ready": bool,
          "need_store": bool,
          "need_index": bool,
          "need_edges": bool,
          "need_embed": bool,
          "atom_store_ready": bool,
          "index_ready": bool,
          "edges_ready": bool,
          "embedder_ready": bool,
        }

    When store is not needed, ``memory_ready`` is True (fabric N/A = OK).
    Component ready flags are always the raw inputs (except atom_store).
    """
    ns = need_store(enabled=enabled, write_atoms=write_atoms)
    ni = need_index(enabled=enabled, write_atoms=write_atoms)
    ne = need_edges(
        enabled=enabled,
        write_atoms=write_atoms,
        backend=backend,
        durable_edges_enabled=durable_edges_enabled,
    )
    nem = need_embed(
        enabled=enabled,
        write_atoms=write_atoms,
        embed_enabled=embed_enabled,
    )
    atom_store_ready = bool(store_open) and bool(store_ok)
    if not ns:
        ready = True
    else:
        ready = (
            atom_store_ready
            and (not ni or bool(index_ready))
            and (not ne or bool(edges_ready))
            and (not nem or bool(embedder_ready))
        )
    return {
        "memory_ready": bool(ready),
        "need_store": ns,
        "need_index": ni,
        "need_edges": ne,
        "need_embed": nem,
        "atom_store_ready": atom_store_ready,
        "index_ready": bool(index_ready),
        "edges_ready": bool(edges_ready),
        "embedder_ready": bool(embedder_ready),
    }


def edges_component_ready(
    *,
    state: str,
    handle: Any | None,
    health: Mapping[str, Any] | None,
    unavailable_type: type | tuple[type, ...] | None = None,
) -> bool:
    """True when EdgeStore is a real ready handle with health.ok.

    Past bug: status claimed ready when backing data absent / Unavailable /
    parity-fail. Rules (design R1):

    - open SM state must be ``ready``
    - handle must not be None / Unavailable*
    - ``health.ok`` must be true (parity mismatch → ok=false → not ready)
    """
    if str(state or "") != "ready":
        return False
    if handle is None:
        return False
    if unavailable_type is not None and isinstance(handle, unavailable_type):
        return False
    if health is None:
        return False
    if not isinstance(health, Mapping):
        return False
    return bool(health.get("ok"))


def format_memory_fabric_cli_line(mem: Mapping[str, Any]) -> str:
    """One-line CLI posture: ``memory: ready|warming|degraded|off …``.

    Independent of chat_ready. Best-effort; never raises.
    """
    try:
        enabled = bool(mem.get("enabled")) or bool(mem.get("write_atoms"))
        if not enabled and not bool(mem.get("need_store", False)):
            # Prefer explicit disabled when flags off.
            if not bool(mem.get("enabled")) and not bool(mem.get("write_atoms")):
                return "memory:      off"

        warming = bool(mem.get("warming") or mem.get("memory_warming"))
        ready = bool(mem.get("memory_ready"))
        store_ok = bool(mem.get("ok")) and bool(mem.get("store_open"))
        edges_ready = bool(mem.get("edges_ready"))
        emb_state = str(
            (mem.get("embedder") or {}).get("state")
            if isinstance(mem.get("embedder"), Mapping)
            else mem.get("embedder_state")
            or "absent"
        )
        edges_state = "ready" if edges_ready else None
        if edges_state is None:
            eo = mem.get("edges_open") if isinstance(mem.get("edges_open"), Mapping) else None
            edges_obj = mem.get("edges") if isinstance(mem.get("edges"), Mapping) else None
            if edges_obj and edges_obj.get("state"):
                edges_state = str(edges_obj.get("state"))
            elif eo and eo.get("state"):
                edges_state = str(eo.get("state"))
            else:
                edges_state = "absent"

        if warming and not ready:
            phase = "warming"
        elif ready:
            phase = "ready"
        elif not enabled:
            phase = "off"
        else:
            phase = "degraded"

        bits = [
            f"store={'ok' if store_ok else 'down'}",
            f"edges={edges_state}",
            f"embedder={emb_state}",
        ]
        if phase == "ready":
            return "memory:      ready"
        if phase == "off":
            return "memory:      off"
        return f"memory:      {phase} ({' '.join(bits)})"
    except Exception:  # noqa: BLE001 — posture is best-effort
        return "memory:      unknown"


__all__ = [
    "compute_memory_ready",
    "edges_component_ready",
    "format_memory_fabric_cli_line",
    "need_edges",
    "need_embed",
    "need_index",
    "need_store",
]
