"""Curated xAI model allowlist and display labels.

Scope: default wire ids / labels for Phase 0 product posture.
Full picker / remote list helpers land with client factories (later PR).
"""

from __future__ import annotations

DEFAULT_XAI_MODEL = "grok-4.5"
DEFAULT_XAI_MODEL_LABEL = "Grok 4.5 Fast"

CURATED_XAI_MODELS: tuple[str, ...] = (
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-non-reasoning",
)

MODEL_LABELS: dict[str, str] = {
    "grok-4.5": "Grok 4.5 Fast",
    "grok-4.3": "Grok 4.3",
    "grok-4.20-0309-non-reasoning": "Grok 4.20 (non-reasoning)",
}


def label_for_model(model_id: str) -> str:
    """Return display label for a wire model id (fallback: the id itself)."""
    return MODEL_LABELS.get(model_id, model_id)
