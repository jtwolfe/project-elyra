"""Smoke tests for elyra_graph Phase 0 (stdlib SVG)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from elyra_graph import GraphingError, plot, quick
from elyra_graph.themes import list_themes

out = ROOT / "out" / "test"
out.mkdir(parents=True, exist_ok=True)


def test_line_and_meta():
    r = plot(
        kind="line",
        data={"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]},
        x="x",
        y="y",
        out=str(out / "line.svg"),
        title="squares",
        backend="svg",
    )
    p = Path(r.path)
    if not p.is_file():
        p = Path.cwd() / r.path
    assert p.is_file(), r.path
    assert r.kind == "line"
    assert r.backend == "svg"
    assert r.meta_path and Path(r.meta_path).is_file()
    print("test_line_and_meta ok", p, p.stat().st_size)


def test_quick():
    r = quick("sin(x)", x=(0, 6.28), n=100, out=str(out / "q.svg"), theme="elyra_dark")
    p = Path(r.path)
    if not p.is_file():
        p = Path.cwd() / r.path
    assert p.is_file() and p.stat().st_size > 500
    print("test_quick ok", p.stat().st_size)


def test_bar_and_pie():
    r = plot(
        kind="bar",
        data={"cat": ["a", "b", "c"], "n": [3, 7, 2]},
        x="cat",
        y="n",
        out=str(out / "bar.svg"),
        title="counts",
    )
    assert Path(r.path).is_file() or (Path.cwd() / r.path).is_file()
    r2 = plot(
        kind="pie",
        data={"label": ["x", "y", "z"], "share": [50, 30, 20]},
        x="label",
        y="share",
        out=str(out / "pie.svg"),
    )
    assert Path(r2.path).is_file() or (Path.cwd() / r2.path).is_file()
    print("test_bar_and_pie ok")


def test_bad_kind():
    try:
        plot(kind="not-a-chart", data={"y": [1, 2]}, out=str(out / "x.svg"), backend="svg")
    except GraphingError as e:
        assert e.code == "E_KIND"
        print("test_bad_kind ok")
        return
    raise AssertionError("expected GraphingError")


def test_themes_listed():
    t = list_themes()
    assert "elyra_light" in t and "elyra_dark" in t
    print("test_themes_listed ok", t)


if __name__ == "__main__":
    test_line_and_meta()
    test_quick()
    test_bar_and_pie()
    test_bad_kind()
    test_themes_listed()
    print("ALL SMOKE PASSED")
