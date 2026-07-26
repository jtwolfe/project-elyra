"""Runtime provider preferences (non-secret) under ``data/runtime/provider.json``.

Scope: load/save model + credential_source UI prefs. Never stores secrets.
Out of scope: merge order with CLI/settings (supervisor/CLI PR), API routes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER_PREFS_REL = Path("runtime") / "provider.json"

_VALID_CREDENTIAL_SOURCES = frozenset({"grok_build", "api_key"})


@dataclass
class ProviderPrefs:
    """Non-secret runtime prefs for model and credential source selection."""

    model: str | None = None
    credential_source: str | None = None


def provider_prefs_path(data_dir: Path) -> Path:
    return Path(data_dir) / PROVIDER_PREFS_REL


def load_provider_prefs(data_dir: Path) -> ProviderPrefs:
    """Load prefs from ``data/runtime/provider.json``.

    Missing or corrupt file → empty prefs (all None). Invalid
    ``credential_source`` values are ignored (treated as unset).
    """
    path = provider_prefs_path(data_dir)
    if not path.is_file():
        return ProviderPrefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("provider prefs load failed (%s): %s", path, exc)
        return ProviderPrefs()
    if not isinstance(raw, dict):
        return ProviderPrefs()

    model: str | None = None
    m = raw.get("model")
    if isinstance(m, str) and m.strip():
        model = m.strip()

    credential_source: str | None = None
    cs = raw.get("credential_source")
    if isinstance(cs, str) and cs.strip() in _VALID_CREDENTIAL_SOURCES:
        credential_source = cs.strip()

    return ProviderPrefs(model=model, credential_source=credential_source)


def save_provider_prefs(data_dir: Path, prefs: ProviderPrefs) -> Path:
    """Persist model / credential_source to provider.json (creates parents).

    Only non-None fields are written (plus ``updated_at``). Does not store
    secrets. Invalid credential_source raises ValueError.
    """
    if prefs.credential_source is not None:
        cs = prefs.credential_source.strip()
        if cs not in _VALID_CREDENTIAL_SOURCES:
            raise ValueError(
                f"credential_source: expected one of "
                f"{sorted(_VALID_CREDENTIAL_SOURCES)}, got {prefs.credential_source!r}"
            )
        prefs = ProviderPrefs(
            model=prefs.model,
            credential_source=cs,
        )

    path = provider_prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    body: dict[str, Any] = {
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if prefs.model is not None and str(prefs.model).strip():
        body["model"] = str(prefs.model).strip()
    if prefs.credential_source is not None:
        body["credential_source"] = prefs.credential_source

    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
