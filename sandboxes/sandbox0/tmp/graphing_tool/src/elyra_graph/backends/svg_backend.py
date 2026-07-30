"""Pure-stdlib SVG backend (no numpy/matplotlib required)."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Sequence

from ..errors import GraphingError
from ..export import safe_out_path, write_meta
from ..palettes import get_palette
from ..types import PlotResult

SUPPORTED = {
    "line", "scatter", "bar", "barh", "hist", "histogram",
    "area", "step", "pie", "donut", "stem",
}


def _xml(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace(chr(34), "&quot;")
    )


def _f(xs: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for v in xs:
        try:
            out.append(float(v))
        except (TypeError, ValueError) as e:
            raise GraphingError("E_DATA", f"non-numeric value: {v!r}") from e
    return out


def _coerce_cols(data: Any) -> dict[str, list[Any]]:
    if data is None:
        raise GraphingError("E_DATA", "data is required")
    if isinstance(data, dict):
        if not data:
            raise GraphingError("E_DATA", "data dict is empty")
        cols = {str(k): list(v) for k, v in data.items()}
        n = len(next(iter(cols.values())))
        if any(len(v) != n for v in cols.values()):
            raise GraphingError("E_DATA", "column length mismatch")
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
    raise GraphingError("E_DATA", f"unsupported data type: {type(data).__name__}")


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if hi == lo:
        hi = lo + 1.0
        lo = lo - 1.0
    span = hi - lo
    step = span / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(step)) if step > 0 else 1.0
    for m in (1, 2, 2.5, 5, 10):
        if mag * m >= step * 0.8:
            step = mag * m
            break
    start = math.floor(lo / step) * step
    ticks = []
    x = start
    for _ in range(50):
        if x >= lo - step * 1e-6:
            ticks.append(x)
        if x > hi + step * 1e-6:
            break
        x += step
    return ticks or [lo, hi]


class SvgBackend:
    name = "svg"

    def render(self, request: dict[str, Any]) -> PlotResult:
        kind = (request.get("kind") or "line").lower()
        if kind not in SUPPORTED:
            raise GraphingError(
                "E_KIND",
                f"unsupported kind for svg backend: {kind}",
                hint=f"supported: {sorted(SUPPORTED)}",
            )
        out = request.get("out")
        if not out:
            raise GraphingError("E_EXPORT", "out path is required")
        path = safe_out_path(out)
        if path.suffix.lower() != ".svg":
            path = path.with_suffix(".svg")

        cols = _coerce_cols(request.get("data"))
        try:
            palette = get_palette(request.get("palette"))
        except KeyError as e:
            raise GraphingError("E_SCHEMA", str(e)) from e

        theme = (request.get("theme") or "elyra_light").lower()
        dark = "dark" in theme
        bg = "#0F1419" if dark else "#FFFFFF"
        fg = "#E7ECF3" if dark else "#1F2A37"
        grid_c = "#243041" if dark else "#E5EAF0"
        axis_c = "#A0AEC0" if dark else "#4B5563"

        figsize = request.get("figsize") or (9, 5.5)
        dpi = int(request.get("dpi") or 96)
        W = int(float(figsize[0]) * dpi)
        H = int(float(figsize[1]) * dpi)
        ml, mr, mt, mb = 64, 24, 48, 56
        pw, ph = W - ml - mr, H - mt - mb

        title = request.get("title") or ""
        xlabel = request.get("xlabel") or request.get("x") or ""
        ylabel = request.get("ylabel") or request.get("y") or ""

        parts: list[str] = []
        parts.append(
            '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'.format(w=W, h=H)
        )
        parts.append('<rect width="100%" height="100%" fill="{0}"/>'.format(bg))
        if title:
            parts.append(
                '<text x="{x:.1f}" y="28" text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="16" fill="{fg}">{t}</text>'.format(
                    x=W / 2, fg=fg, t=_xml(title)
                )
            )

        body = self._draw_kind(
            kind, cols, request, palette, ml, mt, pw, ph, fg, grid_c, axis_c, xlabel, ylabel
        )
        parts.extend(body)
        parts.append("</svg>")
        path.write_text(chr(10).join(parts), encoding="utf-8")

        meta_path = None
        if request.get("meta", True):
            req_meta = {k: v for k, v in request.items() if k != "data"}
            req_meta["data_columns"] = sorted(cols)
            req_meta["data_rows"] = len(next(iter(cols.values()))) if cols else 0
            mp = write_meta(
                path,
                {
                    "kind": kind,
                    "backend": self.name,
                    "theme": theme,
                    "dpi": dpi,
                    "figsize": list(figsize),
                    "path": str(path),
                    "versions": {"elyra_graph": "0.1.0", "backend": "svg-stdlib"},
                    "warnings": ["stdlib-svg"],
                    "request": req_meta,
                },
            )
            meta_path = str(mp)

        root = Path(os.environ.get("ELYRA_WORKSPACE", Path.cwd())).resolve()
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        return PlotResult(
            path=rel,
            meta_path=meta_path,
            kind=kind,
            backend=self.name,
            width_px=W,
            height_px=H,
            warnings=["rendered with pure-stdlib SVG backend (matplotlib not installed)"],
        )

    def _draw_kind(self, kind, cols, request, palette, ml, mt, pw, ph, fg, grid_c, axis_c, xlabel, ylabel):
        if kind in {"pie", "donut"}:
            return self._pie(cols, request, palette, ml, mt, pw, ph, donut=(kind == "donut"), fg=fg)
        if kind in {"hist", "histogram"}:
            return self._hist(cols, request, palette, ml, mt, pw, ph, fg, grid_c, axis_c, xlabel, ylabel)

        x_name = request.get("x")
        y_name = request.get("y")
        color_name = request.get("color")
        if y_name is None:
            for k in cols:
                if k not in {x_name, color_name}:
                    y_name = k
                    break
        if y_name is None:
            raise GraphingError("E_DATA", "could not infer y column")
        y = _f(cols[y_name])
        x_labels = None
        if x_name and x_name in cols:
            try:
                x = _f(cols[x_name])
                x_is_cat = False
            except GraphingError:
                x_labels = [str(v) for v in cols[x_name]]
                x = list(range(len(x_labels)))
                x_is_cat = True
        else:
            x = list(range(len(y)))
            x_labels = [str(i) for i in x]
            x_is_cat = kind in {"bar", "barh"}

        series: list[tuple[str, list[float], list[float], str]] = []
        if color_name and color_name in cols:
            groups = [str(v) for v in cols[color_name]]
            uniq = list(dict.fromkeys(groups))
            for i, g in enumerate(uniq):
                xs, ys = [], []
                for j, gv in enumerate(groups):
                    if gv == g:
                        xs.append(x[j])
                        ys.append(y[j])
                series.append((g, xs, ys, palette[i % len(palette)]))
        else:
            series.append((request.get("label") or y_name, x, y, palette[0]))

        all_x = [v for _, xs, _, _ in series for v in xs]
        all_y = [v for _, _, ys, _ in series for v in ys]
        xmin, xmax = min(all_x), max(all_x)
        if kind in {"bar", "stem"}:
            ymin, ymax = min(0.0, min(all_y)), max(0.0, max(all_y))
        else:
            ymin, ymax = min(all_y), max(all_y)
        if xmax == xmin:
            xmax += 1.0
            xmin -= 1.0
        if ymax == ymin:
            ymax += 1.0
            ymin -= 1.0
        dx = (xmax - xmin) * 0.05 or 1.0
        dy = (ymax - ymin) * 0.08 or 1.0
        if kind not in {"bar"}:
            xmin -= dx
            xmax += dx
        ymin -= dy * 0.15
        ymax += dy

        def sx(v: float) -> float:
            return ml + (v - xmin) / (xmax - xmin) * pw

        def sy(v: float) -> float:
            return mt + ph - (v - ymin) / (ymax - ymin) * ph

        parts = []
        parts.append(
            '<rect x="{0}" y="{1}" width="{2}" height="{3}" fill="none" stroke="{4}"/>'.format(ml, mt, pw, ph, grid_c)
        )
        for ty in _nice_ticks(ymin, ymax):
            if ty < ymin or ty > ymax:
                continue
            yy = sy(ty)
            parts.append(
                '<line x1="{0}" y1="{1:.2f}" x2="{2}" y2="{1:.2f}" stroke="{3}" stroke-width="1"/>'.format(ml, yy, ml + pw, grid_c)
            )
            parts.append(
                '<text x="{0}" y="{1:.2f}" text-anchor="end" font-family="DejaVu Sans, sans-serif" font-size="11" fill="{2}">{3:g}</text>'.format(ml - 8, yy + 4, axis_c, ty)
            )

        if x_is_cat:
            lab_map = {}
            if x_labels is not None:
                for xv, lab in zip(x, x_labels):
                    lab_map[xv] = lab
            for xv in list(dict.fromkeys(x)):
                xx = sx(xv)
                lab = lab_map.get(xv, str(xv))
                parts.append(
                    '<text x="{0:.2f}" y="{1}" text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="11" fill="{2}">{3}</text>'.format(xx, mt + ph + 18, axis_c, _xml(lab))
                )
        else:
            for tx in _nice_ticks(xmin, xmax):
                if tx < xmin or tx > xmax:
                    continue
                xx = sx(tx)
                parts.append(
                    '<line x1="{0:.2f}" y1="{1}" x2="{0:.2f}" y2="{2}" stroke="{3}" stroke-width="1"/>'.format(xx, mt, mt + ph, grid_c)
                )
                parts.append(
                    '<text x="{0:.2f}" y="{1}" text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="11" fill="{2}">{3:g}</text>'.format(xx, mt + ph + 18, axis_c, tx)
                )

        if xlabel:
            parts.append(
                '<text x="{0:.1f}" y="{1}" text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="12" fill="{2}">{3}</text>'.format(ml + pw / 2, mt + ph + 40, fg, _xml(str(xlabel)))
            )
        if ylabel:
            parts.append(
                '<text x="16" y="{0:.1f}" text-anchor="middle" transform="rotate(-90 16 {0:.1f})" font-family="DejaVu Sans, sans-serif" font-size="12" fill="{1}">{2}</text>'.format(mt + ph / 2, fg, _xml(str(ylabel)))
            )

        for label, xs, ys, color in series:
            if kind == "line":
                pts = " ".join("%.2f,%.2f" % (sx(a), sy(b)) for a, b in zip(xs, ys))
                parts.append(
                    '<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>' % (color, pts)
                )
            elif kind == "step":
                if xs:
                    d = ["M %.2f %.2f" % (sx(xs[0]), sy(ys[0]))]
                    for i in range(1, len(xs)):
                        d.append("H %.2f V %.2f" % (sx(xs[i]), sy(ys[i])))
                    parts.append(
                        '<path d="%s" fill="none" stroke="%s" stroke-width="2.5"/>' % (" ".join(d), color)
                    )
            elif kind == "area":
                if xs:
                    pts = " ".join("%.2f,%.2f" % (sx(a), sy(b)) for a, b in zip(xs, ys))
                    z = 0.0 if ymin <= 0 <= ymax else ymin
                    base = "%.2f,%.2f %.2f,%.2f" % (sx(xs[-1]), sy(z), sx(xs[0]), sy(z))
                    parts.append(
                        '<polygon fill="%s" fill-opacity="0.35" stroke="%s" stroke-width="2" points="%s %s"/>' % (color, color, pts, base)
                    )
            elif kind == "scatter":
                for a, b in zip(xs, ys):
                    parts.append(
                        '<circle cx="%.2f" cy="%.2f" r="4" fill="%s" fill-opacity="0.85"/>' % (sx(a), sy(b), color)
                    )
            elif kind == "bar":
                n = max(len(xs), 1)
                bw = (pw / max(n, 1)) * 0.7
                z = sy(0.0) if ymin <= 0 <= ymax else sy(ymin)
                for a, b in zip(xs, ys):
                    x0 = sx(a) - bw / 2
                    top = min(sy(b), z)
                    h = abs(z - sy(b))
                    parts.append(
                        '<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s"/>' % (x0, top, bw, max(h, 1), color)
                    )
            elif kind == "barh":
                n = max(len(xs), 1)
                bh = (ph / max(n, 1)) * 0.7
                vmin, vmax = min(0.0, min(ys)), max(ys)
                if vmax == vmin:
                    vmax += 1.0
                cmin, cmax = min(xs) - 0.5, max(xs) + 0.5

                def hx(v: float) -> float:
                    return ml + (v - vmin) / (vmax - vmin) * pw

                def hy(v: float) -> float:
                    return mt + ph - (v - cmin) / (cmax - cmin) * ph

                for a, b in zip(xs, ys):
                    y_mid = hy(a)
                    x0 = hx(0.0 if vmin <= 0 else vmin)
                    x1 = hx(b)
                    parts.append(
                        '<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s"/>' % (min(x0, x1), y_mid - bh / 2, abs(x1 - x0), bh, color)
                    )
            elif kind == "stem":
                z = sy(0.0) if ymin <= 0 <= ymax else sy(ymin)
                for a, b in zip(xs, ys):
                    parts.append(
                        '<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="2"/>' % (sx(a), z, sx(a), sy(b), color)
                    )
                    parts.append(
                        '<circle cx="%.2f" cy="%.2f" r="3.5" fill="%s"/>' % (sx(a), sy(b), color)
                    )

        if len(series) > 1:
            lx, ly = ml + pw - 120, mt + 12
            for i, (label, _, _, color) in enumerate(series):
                yy = ly + i * 18
                parts.append(
                    '<rect x="%s" y="%s" width="12" height="12" fill="%s"/>' % (lx, yy - 8, color)
                )
                parts.append(
                    '<text x="%s" y="%s" font-family="DejaVu Sans, sans-serif" font-size="11" fill="%s">%s</text>' % (lx + 18, yy + 2, fg, _xml(str(label)))
                )
        return parts

    def _hist(self, cols, request, palette, ml, mt, pw, ph, fg, grid_c, axis_c, xlabel, ylabel):
        col = request.get("y") or request.get("x") or next(iter(cols))
        vals = _f(cols[col])
        bins = request.get("bins") or 10
        if bins == "auto":
            bins = max(5, int(math.sqrt(len(vals))))
        bins = int(bins)
        lo, hi = min(vals), max(vals)
        if hi == lo:
            hi += 1.0
            lo -= 1.0
        width = (hi - lo) / bins
        counts = [0] * bins
        for v in vals:
            i = min(int((v - lo) / width), bins - 1)
            counts[i] += 1
        data = {
            "x": [lo + (i + 0.5) * width for i in range(bins)],
            "y": counts,
        }
        req = dict(request)
        req["x"], req["y"] = "x", "y"
        return self._draw_kind(
            "bar", data, req, palette, ml, mt, pw, ph, fg, grid_c, axis_c,
            xlabel or col, ylabel or "count",
        )

    def _pie(self, cols, request, palette, ml, mt, pw, ph, donut, fg):
        y_name = request.get("y") or next(iter(cols))
        sizes = _f(cols[y_name])
        label_name = request.get("x")
        if label_name and label_name in cols:
            labels = [str(v) for v in cols[label_name]]
        else:
            labels = [str(i) for i in range(len(sizes))]
        total = sum(sizes) or 1.0
        cx, cy = ml + pw / 2, mt + ph / 2
        r = min(pw, ph) * 0.38
        parts: list[str] = []
        ang = -math.pi / 2
        for i, (sz, lab) in enumerate(zip(sizes, labels)):
            sweep = 2 * math.pi * (sz / total)
            a0, a1 = ang, ang + sweep
            x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
            x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
            large = 1 if sweep > math.pi else 0
            color = palette[i % len(palette)]
            parts.append(
                '<path d="M %.2f %.2f L %.2f %.2f A %.2f %.2f 0 %d 1 %.2f %.2f Z" fill="%s"/>' % (cx, cy, x0, y0, r, r, large, x1, y1, color)
            )
            mid = ang + sweep / 2
            lx = cx + r * 1.15 * math.cos(mid)
            ly = cy + r * 1.15 * math.sin(mid)
            parts.append(
                '<text x="%.2f" y="%.2f" text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="11" fill="%s">%s</text>' % (lx, ly, fg, _xml(lab))
            )
            ang = a1
        if donut:
            parts.append(
                '<circle cx="%.2f" cy="%.2f" r="%.2f" fill="#FFFFFF" fill-opacity="0.92"/>' % (cx, cy, r * 0.55)
            )
        return parts

