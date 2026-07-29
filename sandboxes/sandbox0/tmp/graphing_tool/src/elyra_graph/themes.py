"""Matplotlib theme packs."""

from __future__ import annotations

from typing import Any

_THEMES: dict[str, dict[str, Any]] = {
    "elyra_light": {
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FAFBFC",
        "axes.edgecolor": "#C5CDD8",
        "axes.labelcolor": "#1F2A37",
        "axes.grid": True,
        "grid.color": "#E5EAF0",
        "grid.linewidth": 0.8,
        "text.color": "#1F2A37",
        "xtick.color": "#4B5563",
        "ytick.color": "#4B5563",
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "legend.frameon": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 2.0,
        "lines.markersize": 6,
    },
    "elyra_dark": {
        "figure.facecolor": "#0F1419",
        "axes.facecolor": "#151B23",
        "axes.edgecolor": "#2D3748",
        "axes.labelcolor": "#E7ECF3",
        "axes.grid": True,
        "grid.color": "#243041",
        "grid.linewidth": 0.8,
        "text.color": "#E7ECF3",
        "xtick.color": "#A0AEC0",
        "ytick.color": "#A0AEC0",
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "legend.frameon": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 2.0,
        "lines.markersize": 6,
    },
    "minimal": {
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FFFFFF",
        "axes.grid": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": True,
        "axes.spines.bottom": True,
        "font.size": 11,
        "lines.linewidth": 1.75,
    },
    "print_bw": {
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FFFFFF",
        "axes.edgecolor": "#000000",
        "text.color": "#000000",
        "axes.labelcolor": "#000000",
        "xtick.color": "#000000",
        "ytick.color": "#000000",
        "axes.grid": True,
        "grid.color": "#BBBBBB",
        "grid.linestyle": ":",
        "lines.linewidth": 1.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
    },
    "presentation": {
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FFFFFF",
        "font.size": 14,
        "axes.titlesize": 20,
        "axes.labelsize": 16,
        "legend.fontsize": 13,
        "lines.linewidth": 2.8,
        "lines.markersize": 9,
        "axes.grid": True,
        "grid.alpha": 0.35,
        "axes.spines.top": False,
        "axes.spines.right": False,
    },
    "solarized": {
        "figure.facecolor": "#FDF6E3",
        "axes.facecolor": "#EEE8D5",
        "axes.edgecolor": "#93A1A1",
        "text.color": "#657B83",
        "axes.labelcolor": "#586E75",
        "xtick.color": "#657B83",
        "ytick.color": "#657B83",
        "axes.grid": True,
        "grid.color": "#93A1A1",
        "grid.alpha": 0.35,
        "axes.spines.top": False,
        "axes.spines.right": False,
    },
}


def list_themes() -> list[str]:
    return sorted(_THEMES)


def get_theme(name: str | None) -> dict[str, Any]:
    if not name:
        name = "elyra_light"
    key = name.lower().replace("-", "_")
    if key not in _THEMES:
        raise KeyError(f"Unknown theme {name!r}. Known: {', '.join(list_themes())}")
    return dict(_THEMES[key])
