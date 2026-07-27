"""Curated xAI model allowlist, display labels, and remote list helpers.

Scope: default wire ids / labels for Phase 0 product posture;
GET /models under an OpenAI-compatible base (path join, not /v1/models when
base already ends in /v1); picker merge against curated allowlist.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence

DEFAULT_XAI_MODEL = "grok-4.5"
DEFAULT_XAI_MODEL_LABEL = "Grok 4.5"

CURATED_XAI_MODELS: tuple[str, ...] = (
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-non-reasoning",
)

MODEL_LABELS: dict[str, str] = {
    "grok-4.5": "Grok 4.5",
    "grok-4.3": "Grok 4.3",
    "grok-4.20-0309-non-reasoning": "Grok 4.20 (non-reasoning)",
}

# Substrings that mark non-chat model ids (image / voice / media).
_NON_CHAT_MARKERS: tuple[str, ...] = (
    "image",
    "tts",
    "stt",
    "voice",
    "imagine",
)


def label_for_model(model_id: str) -> str:
    """Return display label for a wire model id (fallback: the id itself)."""
    return MODEL_LABELS.get(model_id, model_id)


def _is_chat_model_id(model_id: str) -> bool:
    lower = model_id.lower()
    return not any(marker in lower for marker in _NON_CHAT_MARKERS)


def list_remote_models(
    base_url: str,
    token: str,
    *,
    timeout: float = 30.0,
) -> list[str]:
    """GET ``{base_url.rstrip('/')}/models`` with Bearer auth.

    Path join is intentional: when ``base_url`` is ``https://api.x.ai/v1``,
    the request hits ``https://api.x.ai/v1/models`` — **not** ``/v1/models``
    stacked on a host root that would double ``/v1``.
    """
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"models HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"models connection failed: {exc.reason}") from exc

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("models response is not a JSON object")
    items = data.get("data")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid or mid in seen:
            continue
        if not _is_chat_model_id(mid):
            continue
        seen.add(mid)
        out.append(mid)
    return out


def models_for_picker(
    listed: list[str] | None,
    *,
    fallback: Sequence[str] = CURATED_XAI_MODELS,
    current: str | None = None,
) -> list[str]:
    """Merge remote list with curated order for the glass model picker.

    - Prefer curated ids that appear in ``listed`` (stable product order).
    - Append other chat-looking remote ids not in curated.
    - When ``listed`` is None/empty, use ``fallback``.
    - Ensure ``current`` is present (prepend if missing).
    """
    if listed:
        listed_set = {m for m in listed if isinstance(m, str) and m}
        ordered: list[str] = []
        seen: set[str] = set()
        for mid in fallback:
            if mid in listed_set and mid not in seen:
                ordered.append(mid)
                seen.add(mid)
        for mid in listed:
            if mid not in seen and isinstance(mid, str) and mid:
                ordered.append(mid)
                seen.add(mid)
    else:
        ordered = [m for m in fallback if isinstance(m, str) and m]
        seen = set(ordered)

    if current and current not in seen:
        ordered = [current, *ordered]
    return ordered
