"""Runtime provider preferences (non-secret) under ``data/runtime/provider.json``.

Scope: load/save model + credential_source + reasoning_effort UI prefs.
Never stores secrets.
Out of scope: merge order with CLI/settings (supervisor/CLI PR), API routes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.llm.auth import VALID_SOURCES

logger = logging.getLogger(__name__)

PROVIDER_PREFS_REL = Path("runtime") / "provider.json"

# SSOT: import from auth — never maintain a parallel frozenset (KD14).
_VALID_CREDENTIAL_SOURCES = VALID_SOURCES
_VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high"})
DEFAULT_REASONING_EFFORT = "high"


@dataclass
class ProviderPrefs:
    """Non-secret runtime prefs for model, credential source, reasoning effort."""

    model: str | None = None
    credential_source: str | None = None
    reasoning_effort: str | None = None  # None on load means “unset in file”


def provider_prefs_path(data_dir: Path) -> Path:
    return Path(data_dir) / PROVIDER_PREFS_REL


def resolve_reasoning_effort(raw: str | None) -> str:
    """Map raw/missing/invalid effort to a wire value (default high)."""
    if isinstance(raw, str) and raw.strip() in _VALID_REASONING_EFFORTS:
        return raw.strip()
    return DEFAULT_REASONING_EFFORT


def resolve_reasoning_effort_strict(raw: str | None) -> str:
    """Return validated effort or raise ValueError (API / apply paths)."""
    if not isinstance(raw, str) or raw.strip() not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort: expected one of "
            f"{sorted(_VALID_REASONING_EFFORTS)}, got {raw!r}"
        )
    return raw.strip()


def load_provider_prefs(data_dir: Path) -> ProviderPrefs:
    """Load prefs from ``data/runtime/provider.json``.

    Missing or corrupt file → empty prefs (all None). Invalid
    ``credential_source`` / ``reasoning_effort`` values are ignored
    (treated as unset; no write-back on load).
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

    reasoning_effort: str | None = None
    re = raw.get("reasoning_effort")
    if isinstance(re, str) and re.strip() in _VALID_REASONING_EFFORTS:
        reasoning_effort = re.strip()
    elif re is not None and re != "":
        # Invalid string (including "auto") → None; log once.
        logger.warning(
            "provider prefs: invalid reasoning_effort %r ignored",
            str(re)[:64],
        )

    return ProviderPrefs(
        model=model,
        credential_source=credential_source,
        reasoning_effort=reasoning_effort,
    )


def save_provider_prefs(data_dir: Path, prefs: ProviderPrefs) -> Path:
    """Persist known prefs triple to provider.json (creates parents).

    Only non-None fields are written (plus ``updated_at``). Does not store
    secrets. Invalid credential_source / reasoning_effort raises ValueError.
    """
    model = prefs.model
    credential_source = prefs.credential_source
    reasoning_effort = prefs.reasoning_effort

    if credential_source is not None:
        cs = credential_source.strip()
        if cs not in _VALID_CREDENTIAL_SOURCES:
            raise ValueError(
                f"credential_source: expected one of "
                f"{sorted(_VALID_CREDENTIAL_SOURCES)}, got {credential_source!r}"
            )
        credential_source = cs

    if reasoning_effort is not None:
        re = reasoning_effort.strip()
        if re not in _VALID_REASONING_EFFORTS:
            raise ValueError(
                f"reasoning_effort: expected one of "
                f"{sorted(_VALID_REASONING_EFFORTS)}, got {prefs.reasoning_effort!r}"
            )
        reasoning_effort = re

    path = provider_prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    body: dict[str, Any] = {
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if model is not None and str(model).strip():
        body["model"] = str(model).strip()
    if credential_source is not None:
        body["credential_source"] = credential_source
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort

    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def update_provider_prefs(
    data_dir: Path,
    *,
    model: str | None = None,
    credential_source: str | None = None,
    reasoning_effort: str | None = None,
) -> Path:
    """Load existing prefs, overlay non-None kwargs, save full known triple.

    Must not drop known sibling fields when only one is patched.
    """
    cur = load_provider_prefs(data_dir)
    merged = ProviderPrefs(
        model=model if model is not None else cur.model,
        credential_source=(
            credential_source if credential_source is not None else cur.credential_source
        ),
        reasoning_effort=(
            reasoning_effort if reasoning_effort is not None else cur.reasoning_effort
        ),
    )
    return save_provider_prefs(data_dir, merged)
