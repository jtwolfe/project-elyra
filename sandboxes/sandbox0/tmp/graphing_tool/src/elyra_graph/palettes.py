"""Colorblind-safe and utility palettes."""

from __future__ import annotations

PALETTES: dict[str, list[str]] = {
    # Okabe–Ito
    "okabe_ito": [
        "#0072B2", "#E69F00", "#009E73", "#CC79A7",
        "#56B4E9", "#D55E00", "#F0E442", "#000000",
    ],
    # Paul Tol bright
    "tol_bright": [
        "#4477AA", "#EE6677", "#228833", "#CCBB44",
        "#66CCEE", "#AA3377", "#BBBBBB",
    ],
    "tol_muted": [
        "#332288", "#88CCEE", "#44AA99", "#117733",
        "#999933", "#DDCC77", "#CC6677", "#882255", "#AA4499",
    ],
    "elyra": [
        "#4C78A8", "#F58518", "#E45756", "#72B7B2",
        "#54A24B", "#EECA3B", "#B279A2", "#FF9DA6",
    ],
    "grayscale": ["#111111", "#444444", "#777777", "#AAAAAA", "#DDDDDD"],
}

CMAPS = {
    "sequential": "viridis",
    "sequential_alt": "cividis",
    "diverging": "coolwarm",
    "heat": "magma",
}


def get_palette(name: str | None) -> list[str]:
    if not name:
        return list(PALETTES["okabe_ito"])
    key = name.lower().replace("-", "_")
    if key not in PALETTES:
        known = ", ".join(sorted(PALETTES))
        raise KeyError(f"Unknown palette {name!r}. Known: {known}")
    return list(PALETTES[key])
