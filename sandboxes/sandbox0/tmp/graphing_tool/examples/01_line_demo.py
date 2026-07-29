"""Demo: multi-series line + quick + bar (stdlib)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from elyra_graph import Chart, plot, quick

out = ROOT / "out"
out.mkdir(exist_ok=True)

n = 200
x = [i * 12 / (n - 1) for i in range(n)]
data = {
    "x": x + x,
    "y": [math.sin(v) for v in x] + [math.cos(v) * 0.8 for v in x],
    "series": ["sin"] * n + ["cos"] * n,
}
r1 = plot(
    kind="line",
    data=data,
    x="x",
    y="y",
    color="series",
    out=str(out / "multi_line.svg"),
    title="Sine vs Cosine",
    theme="elyra_light",
    xlabel="t",
    ylabel="amplitude",
)
print("line", r1.path)

r2 = quick(
    "sin(x) * exp(-x/10)",
    x=(0, 40),
    out=str(out / "decay.svg"),
    theme="elyra_dark",
    title="Damped sine",
)
print("quick", r2.path)

r3 = (
    Chart({"cat": ["a", "b", "c", "d"], "n": [4, 7, 2, 9]})
    .encode(x="cat", y="n")
    .mark_bar()
    .theme("presentation")
    .properties(title="Counts")
    .save(str(out / "bars.svg"))
)
print("bar", r3.path)
print("ok")
