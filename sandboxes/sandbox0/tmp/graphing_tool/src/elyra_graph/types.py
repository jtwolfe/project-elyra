"""Shared types and result objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlotResult:
    path: str
    meta_path: str | None
    kind: str
    backend: str
    width_px: int
    height_px: int
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "meta_path": self.meta_path,
            "kind": self.kind,
            "backend": self.backend,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "warnings": list(self.warnings),
            "extra": dict(self.extra),
        }
