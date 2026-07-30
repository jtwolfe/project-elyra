"""Fluent Chart builder (grammar-lite)."""

from __future__ import annotations

from typing import Any

from .api import plot
from .types import PlotResult


class Chart:
    """Small fluent wrapper over plot()."""

    def __init__(self, data: Any):
        self._data = data
        self._enc: dict[str, Any] = {}
        self._marks: list[str] = []
        self._style: dict[str, Any] = {}
        self._kind: str | None = None

    def encode(self, **channels: Any) -> "Chart":
        self._enc.update(channels)
        return self

    def mark_line(self) -> "Chart":
        self._kind = "line"
        self._marks.append("line")
        return self

    def mark_point(self, **kwargs: Any) -> "Chart":
        if self._kind is None:
            self._kind = "scatter"
        self._style.update(kwargs)
        self._marks.append("point")
        return self

    def mark_bar(self) -> "Chart":
        self._kind = "bar"
        return self

    def mark_area(self) -> "Chart":
        self._kind = "area"
        return self

    def mark_hist(self) -> "Chart":
        self._kind = "hist"
        return self

    def theme(self, name: str) -> "Chart":
        self._style["theme"] = name
        return self

    def palette(self, name: str) -> "Chart":
        self._style["palette"] = name
        return self

    def properties(self, **kwargs: Any) -> "Chart":
        self._style.update(kwargs)
        return self

    def save(self, path: str, **kwargs: Any) -> PlotResult:
        kind = self._kind or "line"
        if "line" in self._marks and "point" in self._marks:
            kind = "line"
            self._style.setdefault("alpha", 0.95)
        opts = {**self._style, **kwargs}
        return plot(
            kind=kind,
            data=self._data,
            x=self._enc.get("x"),
            y=self._enc.get("y"),
            color=self._enc.get("color"),
            out=path,
            size=self._enc.get("size"),
            **opts,
        )
