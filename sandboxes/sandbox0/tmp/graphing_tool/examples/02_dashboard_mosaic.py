"""Demo: several chart kinds into out/ (stdlib)."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from elyra_graph import plot

out = ROOT / "out"
out.mkdir(exist_ok=True)
rng = random.Random(0)

vals = [rng.gauss(0, 1) for _ in range(1000)]
plot(kind="hist", data={"v": vals}, y="v",
     out=str(out / "hist.svg"), title="Normal draws", bins=30)

xs = [rng.gauss(0, 1) for _ in range(200)]
ys = [0.6 * x + rng.gauss(0, 0.5) for x in xs]
plot(kind="scatter", data={"x": xs, "y": ys}, x="x", y="y",
     out=str(out / "scatter.svg"), title="Scatter", theme="solarized")

plot(kind="pie",
     data={"label": ["alpha", "beta", "gamma"], "share": [45, 30, 25]},
     x="label", y="share", out=str(out / "pie.svg"), title="Shares")

plot(kind="area",
     data={"x": list(range(30)), "y": [abs(math.sin(i/4)) for i in range(30)]},
     x="x", y="y", out=str(out / "area.svg"), title="Area", theme="elyra_dark")

print("dashboard demos written to", out)
