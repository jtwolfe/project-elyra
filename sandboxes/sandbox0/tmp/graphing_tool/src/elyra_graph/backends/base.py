"""Backend protocol."""

from __future__ import annotations

from typing import Any, Protocol

from ..types import PlotResult


class Backend(Protocol):
    name: str

    def render(self, request: dict[str, Any]) -> PlotResult: ...
