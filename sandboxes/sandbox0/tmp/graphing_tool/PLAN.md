# Elyra Graphing Tool — Feature Plan & Architecture

**Status:** design + sandbox scaffold (not promoted as a host tool yet)  
**Location:** `tmp/graphing_tool/`  
**Audience:** Elyra sandbox / future `create-tool` promotion  
**Date:** 2026-07-29  
**Requested by:** Jim

---

## 1. Intent

A highly featureful, sandbox-first **graphing toolkit** that Elyra can call to:

- turn tabular / array / expression data into publication-quality charts
- support exploratory analysis (stats overlays, facets, dual axes)
- export PNG/SVG/PDF/HTML (interactive) into the sandbox for `speak` attachments
- stay deterministic, testable, and reversible — no silent network, no host escape

This is **not** a BI product. It is a competent plotting engine with a clean API, good defaults, and deep knobs when needed.

---

## 2. Design principles

1. **Data in, artifact out** — primary contract is structured input → file path(s) + metadata.
2. **Sensible defaults, full escape hatches** — one-liners for common charts; grammar/layer API for custom work.
3. **Backend pluggable** — Matplotlib as default (ubiquitous, static); optional Plotly/Altair for interactive HTML.
4. **Reproducible** — seed, style pack, explicit rcParams snapshot in sidecar JSON.
5. **Sandbox-native** — write under `tmp/plots/` (or caller path); return paths suitable for glass attachments.
6. **Fail loud** — validate schemas; never return empty PNGs on bad input.
7. **Composable** — charts, subplots, animations, and multi-page reports share one mark/scale model.

---

## 3. Feature matrix (target)

### 3.1 Chart types

| Family | Types |
|--------|--------|
| Categorical | bar, grouped bar, stacked bar, horizontal bar, lollipop, dot, Pareto |
| Distribution | histogram, KDE, ECDF, box, violin, strip/swarm, ridgeline, quantile-quantile |
| Relationship | scatter, bubble, line, step, area, stacked area, streamgraph, hexbin, 2D density, regression + CI |
| Ranking / part-whole | pie, donut, treemap, sunflower, waffle, nightingale |
| Multivariate | parallel coordinates, radar/spider, pair plot / SPLOM, Andrews curves |
| Temporal | time series, multi-series, range/band, candlestick/OHLC, calendar heatmap, horizon |
| Geospatial (phase 2) | choropleth, point map, hex map (requires geo deps) |
| Hierarchical | sunburst, icicle, dendrogram, circle pack |
| Graph/network (phase 2) | force-directed, arc, adjacency heatmap, Sankey, alluvial |
| 3D (optional) | surface, scatter3d, wireframe (matplotlib/plotly) |
| Specialized | confusion matrix, ROC/PR curves, residual plots, control charts (Shewhart), waterfall, funnel, gauge, bullet |

### 3.2 Grammar of graphics layer

- **Data** — columns, long/wide pivot helpers
- **Marks** — point, line, bar, area, text, rect, tile, errorbar, band, hline/vline, segment, path
- **Encodings** — x, y, color, size, shape, facet_row, facet_col, frame (animation), label, order, alpha
- **Scales** — linear, log, symlog, asinh, power, time, categorical, manual limits, nice breaks
- **Stats transforms** — bin, smooth (LOESS/lowess), density, aggregate (mean/sum/count/median), stack, dodge, jitter, cumulative, rolling
- **Coords** — cartesian, polar, flipped, twin-x / twin-y
- **Faceting** — wrap, grid, shared/free scales
- **Guides** — titles, subtitles, captions, axis labels, legends, colorbars, annotations, inset zooms

### 3.3 Styling & themes

- Built-in themes: `elyra_light`, `elyra_dark`, `print_bw`, `solarized`, `presentation`, `minimal`
- Colorblind-safe palettes (Okabe–Ito, Tol, ColorBrewer qualitative/sequential/diverging)
- Custom hex palettes + continuous colormaps (viridis, magma, cividis, custom)
- Typography controls (family, sizes, weight); mathtext / Unicode
- Grid, spines, aspect ratio, DPI, tight_layout / constrained_layout
- Brand kit: logo watermark, footer, consistent rc dump

### 3.4 Interactivity (HTML backend)

- Hover tooltips, zoom/pan, legend toggle
- Crossfilter-lite linked selections (phase 2)
- Export buttons (Plotly)
- Notebook-friendly JSON specs (Altair/Vega-Lite path)

### 3.5 Data I/O

- Accept: list/dict rows, column dicts, CSV/TSV/JSON paths, numpy arrays, simple expressions (`y = sin(x)`)
- Missing data policies: drop, interpolate, zero-fill, sentinel
- Datetime parsing, categorical ordering, unit-aware labels (optional pint later)
- Outlier flagging helpers (IQR, z-score) as optional layer

### 3.6 Annotation & narrative

- Point/segment callouts with arrows
- Shaded regions (recessions, thresholds)
- Statistical badges (n=, r², p-value) from fitted models
- Reference lines (mean, median, targets, spec limits)
- Subplot letters (A/B/C) for paper figures

### 3.7 Layout & composition

- Single axes, GridSpec, mosaic layouts (`"AB;CC"`)
- Inset axes, broken axes
- Multi-figure reports → PDF pages or HTML dashboard shell
- Equal size export presets: slide 16:9, paper column, square social, retina 2x

### 3.8 Animation & small multiples

- Frame encoding → GIF/MP4 (matplotlib writers) or Plotly frames
- Ridge / small-multiple time evolution
- Easing and fps controls

### 3.9 Analysis helpers (plot-adjacent)

- Quick `describe()` table render as figure
- Correlation heatmap with hierarchical clustering order
- PCA biplot (phase 2)
- Seasonal decompose plot (phase 2)
- Confusion / classification report heatmap

### 3.10 Exports & artifacts

| Format | Backend | Notes |
|--------|---------|--------|
| PNG | mpl | default glass attachment |
| SVG | mpl | crisp docs |
| PDF | mpl | print |
| HTML | plotly/altair | interactive |
| JSON | altair/vega | editable spec |
| CSV | — | data echo beside plot |
| sidecar `.meta.json` | — | params, versions, hash |

### 3.11 CLI & programmable API

```text
# high-level
plot(kind="line", data=df, x="t", y="value", color="series", out="tmp/plots/a.png")

# grammar
(Chart(df)
  .encode(x="t:T", y="value:Q", color="series:N")
  .mark_line()
  .mark_point(alpha=0.4)
  .theme("elyra_dark")
  .save("tmp/plots/a.png", dpi=160))

# expression quickplot
quick("sin(x) * exp(-x/10)", x=(0, 40), out="tmp/plots/decay.png")
```

### 3.12 Quality & ops

- Golden-image tests (RMS tolerance) for themes/chart kinds
- Schema validation (jsonschema) on request payloads
- Deterministic fonts (DejaVu) in headless sandbox
- Memory guards on huge series (downsample strategies: LTTB, min-max buckets)
- Structured errors: `GraphingError` with code + hint

---

## 4. Architecture

```text
┌─────────────────────────────────────────────────────────┐
│  API surface                                             │
│  plot() / Chart / quick() / report() / tool runner I/O   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  Request normalize + schema validate                     │
│  (pandas/numpy coerce, dtypes, limits)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  Grammar resolve                                         │
│  encodings → scales → stats → marks → guides → layout    │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   MplBackend         PlotlyBackend      AltairBackend
   (static)           (HTML/JSON)        (Vega-Lite)
          └─────────────────┼─────────────────┘
                            ▼
                   ArtifactWriter
                   paths + meta.json + optional data.csv
```

### 4.1 Package layout (scaffold)

```text
tmp/graphing_tool/
  PLAN.md                 ← this document
  README.md               ← how to run the scaffold
  pyproject.toml          ← optional package metadata (local)
  schema/
    plot_request.schema.json
  src/elyra_graph/
    __init__.py
    api.py                ← plot(), quick()
    chart.py              ← Chart builder
    types.py              ← enums, dataclasses
    validate.py           ← schema + semantic checks
    data.py               ← load/coerce/pivot
    stats.py              ← bin, smooth, aggregate
    scales.py
    themes.py
    palettes.py
    backends/
      __init__.py
      base.py
      mpl_backend.py
      plotly_backend.py   ← stub
      altair_backend.py   ← stub
    export.py
    errors.py
    downsample.py
  examples/
    01_line_demo.py
    02_dashboard_mosaic.py
  tests/
    test_api_smoke.py
  out/                    ← generated plots (gitignored mentally)
```

### 4.2 Tool-runner shape (future promote)

When promoted via `create-tool`, the host-facing tool might look like:

**Name:** `graph_plot`  
**Args (sketch):**

```json
{
  "kind": "line",
  "data": {"x": [1,2,3], "y": [1,4,9]},
  "x": "x",
  "y": "y",
  "title": "Squares",
  "theme": "elyra_light",
  "out": "tmp/plots/squares.png",
  "width": 10,
  "height": 6,
  "dpi": 144,
  "backend": "mpl"
}
```

**Returns:** `{ ok, path, meta_path, width_px, height_px, warnings[] }`

Promotion is **out of scope for this drop** — Jim asked for plan + sandbox tmp artifact only.

---

## 5. Phased delivery

### Phase 0 — this drop (done in tmp)
- Full plan document
- Scaffold package with working Matplotlib path for: line, scatter, bar, hist, heatmap, multi-series
- Themes + Okabe–Ito palette
- `plot()` + `quick()` + sidecar meta
- One smoke test + example script

### Phase 1 — core completeness
- Box/violin, area, pie/donut, twin axis, facets
- Annotations + reference lines
- LTTB downsample
- Golden tests for 8 chart kinds

### Phase 2 — interactive + grammar depth
- Plotly HTML export
- Altair spec export
- Full stats transforms + faceting grid
- Animation GIF

### Phase 3 — advanced
- Network / Sankey
- Geo
- Report builder (multi-page PDF)
- Host tool draft → verify → promote

---

## 6. Dependencies

| Package | Role | Phase |
|---------|------|-------|
| matplotlib | default renderer | 0 |
| numpy | arrays | 0 |
| (stdlib csv/json) | I/O | 0 |
| pandas | tables (optional soft dep) | 1 |
| jsonschema | request validation | 1 |
| plotly | interactive | 2 |
| altair + vl-convert | grammar/HTML | 2 |
| pillow | GIF/compose | 2 |
| networkx | graphs | 3 |
| scipy | KDE/smooth extras | 1–2 |

Sandbox note: use `sandbox_pip_update` only for allowlisted names when promoting beyond stdlib+matplotlib/numpy already present.

---

## 7. API sketch (normative for scaffold)

### 7.1 `plot(**kwargs) -> PlotResult`

Required:
- `kind`: str
- `data`: mapping of column → list, or list of row dicts, or path
- `out`: sandbox-relative path

Common optional:
- `x`, `y`, `color`, `size`, `fx`, `fy`
- `title`, `xlabel`, `ylabel`, `subtitle`, `caption`
- `theme`, `palette`, `figsize`, `dpi`
- `xlim`, `ylim`, `xscale`, `yscale`
- `legend`, `grid`, `tight`
- `backend`: `mpl` | `plotly` | `altair`
- `meta`: bool (default True) write sidecar

### 7.2 `Chart(data)` builder

Fluent methods mirroring grammar; `.save(path)` / `.show()` (show no-ops or saves preview in headless).

### 7.3 `quick(expr, x=(min,max), n=500, **style) -> PlotResult`

Safe expression eval with `numpy` ufuncs only (no `eval` of arbitrary Python).

### 7.4 `PlotResult`

```python
@dataclass
class PlotResult:
    path: str
    meta_path: str | None
    kind: str
    backend: str
    width_px: int
    height_px: int
    warnings: list[str]
```

---

## 8. Error codes

| Code | Meaning |
|------|---------|
| `E_SCHEMA` | request failed validation |
| `E_DATA` | empty/mismatched columns |
| `E_KIND` | unknown chart kind |
| `E_BACKEND` | backend unavailable |
| `E_RENDER` | matplotlib/plotly failure |
| `E_EXPORT` | path/IO failure |
| `E_EXPR` | unsafe or invalid quick() expression |
| `E_LIMIT` | data too large without downsample policy |

---

## 9. Security & safety

- No shell; no pickle loads
- Expression sandbox: allowlist names (`x`, `t`, `np`, sin/cos/…)
- Output paths must stay under workspace (resolve + prefix check)
- No network in render path
- Cap rows default 250_000 with explicit `downsample=` opt-in strategies

---

## 10. Acceptance criteria (for a future "done" goal)

- [ ] ≥12 chart kinds render deterministic PNGs in headless sandbox
- [ ] Theme switch changes style smoke tests
- [ ] Sidecar meta includes library versions + request hash
- [ ] `quick("sin(x)")` works
- [ ] Bad input raises typed errors (no crash traceback to glass)
- [ ] Example scripts run via `python examples/01_line_demo.py`
- [ ] README documents API and limits
- [ ] (Later) tool package verify green + promote

---

## 11. Non-goals (explicit)

- Full Tableau/PowerBI replacement
- Real-time streaming server
- GPU dashboards
- Editing plots via GUI
- Automatic statistical significance storytelling without request

---

## 12. Open decisions for Jim

1. **Promote to host tool soon?** or keep as importable sandbox library?
2. **Pandas required** or numpy-only core?
3. **Interactive HTML** priority vs static-only for glass attachments?
4. Any **must-have chart** for first real use (e.g. time series with bands)?

---

## 13. Implementation notes for Phase 0 scaffold

- Prefer pure functions + small classes
- Matplotlib Agg backend forced
- Default DPI 144, figsize (9, 5.5)
- File names create parents
- Meta JSON: `{created_at, kind, backend, dpi, figsize, request, versions}`

---

*End of plan. Scaffold code lives beside this file.*
