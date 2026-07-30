"""Data coercion and column helpers (stdlib)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from .errors import GraphingError


def _from_path(path: str | Path) -> dict[str, list[Any]]:
    p = Path(path)
    if not p.is_file():
        raise GraphingError("E_DATA", f"Data file not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        return coerce_data(raw)
    if suffix in {".csv", ".tsv"}:
        delim = "\t" if suffix == ".tsv" else ","
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delim)
            rows = list(reader)
        if not rows:
            raise GraphingError("E_DATA", "CSV/TSV contained no rows")
        cols: dict[str, list[Any]] = {k: [] for k in rows[0].keys()}
        for row in rows:
            for k, v in row.items():
                cols[k].append(_maybe_number(v))
        return cols
    raise GraphingError("E_DATA", f"Unsupported data file type: {suffix}")


def _maybe_number(v: Any) -> Any:
    if v is None:
        return math.nan
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s == "":
        return math.nan
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        return float(s)
    except ValueError:
        return s


def coerce_data(data: Any) -> dict[str, list[Any]]:
    """Normalize supported data inputs to column -> list."""
    if data is None:
        raise GraphingError("E_DATA", "data is required")
    if isinstance(data, (str, Path)):
        data = _from_path(data)
    if isinstance(data, dict):
        if not data:
            raise GraphingError("E_DATA", "data dict is empty")
        if all(isinstance(v, (list, tuple)) for v in data.values()):
            lengths = {k: len(v) for k, v in data.items()}
            n = next(iter(lengths.values()))
            if any(L != n for L in lengths.values()):
                raise GraphingError("E_DATA", "column length mismatch", hint=str(lengths))
            return {str(k): list(v) for k, v in data.items()}
        # tolerate array-like with tolist()
        cols: dict[str, list[Any]] = {}
        for k, v in data.items():
            if hasattr(v, "tolist"):
                cols[str(k)] = list(v.tolist())
            elif isinstance(v, (list, tuple)):
                cols[str(k)] = list(v)
            else:
                raise GraphingError("E_DATA", "dict data must map column names to sequences")
        lengths = {k: len(v) for k, v in cols.items()}
        n = next(iter(lengths.values()))
        if any(L != n for L in lengths.values()):
            raise GraphingError("E_DATA", "column length mismatch", hint=str(lengths))
        return cols
    if isinstance(data, list):
        if not data:
            raise GraphingError("E_DATA", "data list is empty")
        if isinstance(data[0], dict):
            keys = list(data[0].keys())
            cols = {k: [] for k in keys}
            for row in data:
                for k in keys:
                    cols[k].append(row.get(k))
            return cols
        return {"y": list(data), "x": list(range(len(data)))}
    raise GraphingError("E_DATA", f"Unsupported data type: {type(data).__name__}")


def require_columns(cols: dict[str, list[Any]], *names: str) -> None:
    missing = [n for n in names if n and n not in cols]
    if missing:
        raise GraphingError(
            "E_DATA",
            f"missing columns: {missing}",
            hint=f"available: {sorted(cols)}",
        )


def as_float(a: list[Any]) -> list[float]:
    out: list[float] = []
    for v in a:
        try:
            out.append(float(v))
        except (TypeError, ValueError) as e:
            raise GraphingError("E_DATA", "could not cast column to float", hint=str(e)) from e
    return out
