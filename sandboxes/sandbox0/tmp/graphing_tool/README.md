# elyra_graph (sandbox scaffold)

Highly featureful graphing toolkit plan + working Phase 0 implementation.

## Layout

- `PLAN.md` — full feature matrix, architecture, phases
- `src/elyra_graph/` — importable library
- `examples/` — demos
- `tests/` — smoke tests
- `out/` — sample render output

## Quick start

```bash
cd /workspace  # sandbox root
PYTHONPATH=tmp/graphing_tool/src python -c "
from elyra_graph import plot, quick
import math, random
r = quick('sin(x) * exp(-x/10)', x=(0, 40), out='tmp/graphing_tool/out/decay.svg', theme='elyra_dark')
print(r)
rng = random.Random(0)
r2 = plot(kind='scatter',
          data={'x': list(range(20)), 'y': [rng.gauss(0,1) for _ in range(20)]},
          x='x', y='y', out='tmp/graphing_tool/out/scatter.svg', title='Noise')
print(r2.path)
"
```

Or run:

```bash
PYTHONPATH=tmp/graphing_tool/src python tmp/graphing_tool/examples/01_line_demo.py
PYTHONPATH=tmp/graphing_tool/src python tmp/graphing_tool/tests/test_api_smoke.py
```

## API

### `plot(kind, data, *, x, y, color, out, **style) -> PlotResult`

Stdlib SVG kinds (always): line, scatter, bar, barh, hist, area, step, pie, donut, stem.
Matplotlib kinds (when available): + box, heatmap, hexbin, errorbar, violin, contour, imshow.

### `quick(expr, x=(min,max), n=500, out=...) -> PlotResult`

Safe expression plot (`sin`, `exp`, `log`, `pi`, …).

### `Chart(data).encode(...).mark_line().theme(...).save(path)`

Fluent grammar-lite builder.

## Themes

`elyra_light` (default), `elyra_dark`, `minimal`, `print_bw`, `presentation`, `solarized`

## Palettes

`okabe_ito` (default), `tol_bright`, `tol_muted`, `elyra`, `grayscale`

## Status

**Runtime note:** guest sandbox currently lacks numpy/matplotlib on the pip allowlist,
so Phase 0 defaults to a pure-stdlib **SVG backend** (`backend="auto"|"svg"`).
A full Matplotlib backend is implemented and will activate when those packages are available.

Sandbox drop only — **not** promoted as a host tool yet. See PLAN.md phase gates
and open decisions for Jim.
