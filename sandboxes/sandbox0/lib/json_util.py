"""JSON helpers for sandbox tool implementations."""

from __future__ import annotations

import json
from typing import Any


def load_json(text: str) -> Any:
    return json.loads(text)


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)
