"""Artifact writing and sidecar metadata."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import GraphingError


def _workspace_root() -> Path:
    return Path(os.environ.get("ELYRA_WORKSPACE", Path.cwd())).resolve()


def safe_out_path(out: str | Path) -> Path:
    root = _workspace_root()
    p = Path(out)
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise GraphingError(
            "E_EXPORT",
            "refusing to write outside workspace",
            hint=str(p),
        ) from e
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if path.suffix.lower() == ".meta.json":
        meta_path = path.with_name(path.name + ".meta.json")
    # Prefer sibling: plot.png -> plot.png.meta.json is ugly; use plot.meta.json
    meta_path = path.with_name(path.stem + ".meta.json")
    body = dict(payload)
    body.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    raw = json.dumps(body, indent=2, sort_keys=True, default=str)
    body["request_sha256"] = hashlib.sha256(
        json.dumps(payload.get("request", {}), sort_keys=True, default=str).encode()
    ).hexdigest()
    meta_path.write_text(json.dumps(body, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return meta_path
