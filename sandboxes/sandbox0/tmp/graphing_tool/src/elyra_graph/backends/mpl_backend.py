"""Matplotlib static backend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

from ..data import as_float, coerce_data, require_columns
from ..downsample import apply_limit
from ..errors import GraphingError
from ..export import safe_out_path, write_meta
from ..palettes import CMAPS, get_palette
from ..themes import get_theme
from ..types import PlotResult

SUPPORTED = {
    "line", "scatter", "bar", "barh", "hist", "histogram", "box", "area",
    "step", "heatmap", "pie", "donut", "hexbin", "errorbar", "stem",
    "violin", "contour", "imshow",
}

class MplBackend:
    name = "mpl"

    def render(self, request: dict[str, Any]) -> PlotResult:
        kind = (request.get("kind") or "line").lower()
        if kind not in SUPPORTED:
            raise GraphingError(
                "E_KIND",
                f"unsupported kind for mpl backend: {kind}",
                hint=f"supported: {sorted(SUPPORTED)}",
            )

        warnings: list[str] = []
        theme_name = request.get("theme") or "elyra_light"
        try:
            theme = get_theme(theme_name)
        except KeyError as e:
            raise GraphingError("E_SCHEMA", str(e)) from e

        palette_name = request.get("palette")
        try:
            palette = get_palette(palette_name)
        except KeyError as e:
            raise GraphingError("E_SCHEMA", str(e)) from e

        figsize = tuple(request.get("figsize") or (9, 5.5))
        dpi = int(request.get("dpi") or 144)
        out = request.get("out")
        if not out:
            raise GraphingError("E_EXPORT", "out path is required")

        path = safe_out_path(out)
        data = coerce_data(request.get("data"))
        x_name = request.get("x")
        y_name = request.get("y")
        color_name = request.get("color")

        with plt.rc_context(theme):
            fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
            try:
                self._draw(
                    ax, kind=kind, data=data, x_name=x_name, y_name=y_name,
                    color_name=color_name, palette=palette, request=request,
                    warnings=warnings,
                )
                self._style_ax(ax, request)
                tight = request.get("tight", True)
                if tight:
                    fig.tight_layout()
                fig.savefig(path, dpi=dpi, bbox_inches="tight" if tight else None)
            except GraphingError:
                raise
            except Exception as e:  # noqa: BLE001
                raise GraphingError("E_RENDER", "matplotlib render failed", hint=str(e)) from e
            finally:
                plt.close(fig)

        width_px = int(figsize[0] * dpi)
        height_px = int(figsize[1] * dpi)
        meta_path = None
        if request.get("meta", True):
            versions = {"matplotlib": matplotlib.__version__, "numpy": np.__version__, "elyra_graph": "0.1.0"}
            req_meta = {k: v for k, v in request.items() if k != "data"}
            req_meta["data_columns"] = sorted(data.keys())
            req_meta["data_rows"] = int(len(next(iter(data.values())))) if data else 0
            mp = write_meta(path, {
                "kind": kind, "backend": self.name, "theme": theme_name,
                "palette": palette_name or "okabe_ito", "dpi": dpi,
                "figsize": list(figsize), "path": str(path), "versions": versions,
                "warnings": warnings, "request": req_meta,
            })
            meta_path = str(mp)

        root = Path(os.environ.get("ELYRA_WORKSPACE", Path.cwd())).resolve()
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        return PlotResult(
            path=rel, meta_path=meta_path, kind=kind, backend=self.name,
            width_px=width_px, height_px=height_px, warnings=warnings,
        )

    def _style_ax(self, ax, request: dict[str, Any]) -> None:
        if title := request.get("title"):
            ax.set_title(title)
        if xl := request.get("xlabel"):
            ax.set_xlabel(xl)
        elif request.get("x"):
            ax.set_xlabel(str(request.get("x")))
        if yl := request.get("ylabel"):
            ax.set_ylabel(yl)
        elif request.get("y"):
            ax.set_ylabel(str(request.get("y")))
        if sub := request.get("subtitle"):
            ax.text(0.0, 1.02, sub, transform=ax.transAxes, fontsize=10, alpha=0.75, va="bottom")
        if request.get("grid") is False:
            ax.grid(False)
        if xlim := request.get("xlim"):
            ax.set_xlim(xlim)
        if ylim := request.get("ylim"):
            ax.set_ylim(ylim)
        if xscale := request.get("xscale"):
            ax.set_xscale(xscale)
        if yscale := request.get("yscale"):
            ax.set_yscale(yscale)
        if request.get("caption"):
            ax.figure.text(0.5, 0.01, str(request["caption"]), ha="center", fontsize=9, alpha=0.8)

    def _series_xy(self, data, x_name, y_name, request, warnings):
        if y_name is None:
            for k in data:
                if k != x_name:
                    y_name = k
                    break
        if y_name is None:
            raise GraphingError("E_DATA", "could not infer y column")
        require_columns(data, y_name)
        y = as_float(data[y_name])
        if x_name:
            require_columns(data, x_name)
            try:
                x = as_float(data[x_name])
            except GraphingError:
                x = np.arange(len(y))
                warnings.append(f"x column {x_name!r} not numeric; using index")
        else:
            x = np.arange(len(y))
        max_points = int(request.get("max_points") or 250_000)
        strategy = request.get("downsample", "lttb")
        x, y, w = apply_limit(x, y, max_points=max_points, strategy=strategy)
        warnings.extend(w)
        return x, y

    def _draw(self, ax, *, kind, data, x_name, y_name, color_name, palette, request, warnings):
        if kind in {"pie", "donut"}:
            self._pie(ax, data, y_name, x_name, palette, donut=(kind == "donut"), request=request)
            return
        if kind in {"heatmap", "imshow"}:
            self._heatmap(ax, data, request)
            return
        if kind in {"hist", "histogram"}:
            self._hist(ax, data, y_name or x_name, palette, request)
            return
        if kind == "box":
            self._box(ax, data, y_name, color_name, palette, request)
            return
        if kind == "violin":
            self._violin(ax, data, y_name, palette)
            return
        if kind == "hexbin":
            x, y = self._series_xy(data, x_name, y_name, request, warnings)
            hb = ax.hexbin(x, y, gridsize=int(request.get("gridsize") or 30), cmap=CMAPS["heat"], mincnt=1)
            ax.figure.colorbar(hb, ax=ax, label=request.get("colorbar_label") or "count")
            return
        if kind == "contour":
            self._contour(ax, data, request)
            return

        if color_name and color_name in data:
            groups = data[color_name]
            uniq = list(dict.fromkeys([str(g) for g in groups]))
            for i, g in enumerate(uniq):
                mask = np.array([str(v) == g for v in groups])
                sub = {k: v[mask] for k, v in data.items()}
                color = palette[i % len(palette)]
                self._cartesian_mark(ax, kind, sub, x_name, y_name, color=color, label=g, request=request, warnings=warnings)
            if request.get("legend", True):
                ax.legend(title=color_name)
            return

        self._cartesian_mark(
            ax, kind, data, x_name, y_name, color=palette[0],
            label=request.get("label"), request=request, warnings=warnings,
        )

    def _cartesian_mark(self, ax, kind, data, x_name, y_name, *, color, label, request, warnings):
        x, y = self._series_xy(data, x_name, y_name, request, warnings)
        alpha = float(request.get("alpha") or 1.0)
        if kind == "line":
            ax.plot(x, y, color=color, label=label, alpha=alpha)
        elif kind == "step":
            ax.step(x, y, color=color, label=label, alpha=alpha, where=request.get("where") or "mid")
        elif kind == "area":
            ax.fill_between(x, y, color=color, label=label, alpha=min(alpha, 0.45))
            ax.plot(x, y, color=color, alpha=alpha)
        elif kind == "scatter":
            sizes = None
            sn = request.get("size")
            if sn and sn in data:
                sraw = as_float(data[sn])
                sizes = 20 + 80 * (sraw - np.nanmin(sraw)) / (np.ptp(sraw) + 1e-12)
            ax.scatter(x, y, color=color, label=label, alpha=alpha, s=sizes)
        elif kind == "bar":
            ax.bar(x, y, color=color, label=label, alpha=alpha)
        elif kind == "barh":
            ax.barh(x, y, color=color, label=label, alpha=alpha)
        elif kind == "stem":
            m = ax.stem(x, y, label=label)
            plt.setp(m.markerline, color=color)
            plt.setp(m.stemlines, color=color)
            plt.setp(m.baseline, color="#888888")
        elif kind == "errorbar":
            yerr = None
            en = request.get("yerr")
            if en and en in data:
                yerr = as_float(data[en])
            ax.errorbar(x, y, yerr=yerr, color=color, label=label, alpha=alpha, capsize=3)
        else:
            raise GraphingError("E_KIND", f"unhandled kind: {kind}")

        if request.get("regress") and kind in {"scatter", "line"}:
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() >= 2:
                coeff = np.polyfit(x[ok], y[ok], int(request.get("regress_degree") or 1))
                xx = np.linspace(np.nanmin(x[ok]), np.nanmax(x[ok]), 100)
                ax.plot(xx, np.polyval(coeff, xx), color=color, linestyle="--", alpha=0.85)

    def _hist(self, ax, data, col, palette, request):
        if not col:
            col = next(iter(data))
        require_columns(data, col)
        vals = as_float(data[col])
        bins = request.get("bins") or "auto"
        ax.hist(
            vals[np.isfinite(vals)], bins=bins, color=palette[0],
            alpha=float(request.get("alpha") or 0.85), edgecolor="white",
        )

    def _box(self, ax, data, y_name, color_name, palette, request):
        if color_name and color_name in data:
            groups = data[color_name]
            uniq = list(dict.fromkeys([str(g) for g in groups]))
            ycol = y_name or next(k for k in data if k != color_name)
            series, labels = [], []
            for g in uniq:
                mask = np.array([str(v) == g for v in groups])
                series.append(as_float(data[ycol])[mask])
                labels.append(g)
            bp = ax.boxplot(series, tick_labels=labels, patch_artist=True)
            for i, patch in enumerate(bp["boxes"]):
                patch.set_facecolor(palette[i % len(palette)])
        else:
            col = y_name or next(iter(data))
            bp = ax.boxplot([as_float(data[col])], tick_labels=[col], patch_artist=True)
            bp["boxes"][0].set_facecolor(palette[0])

    def _violin(self, ax, data, y_name, palette):
        col = y_name or next(iter(data))
        vals = as_float(data[col])
        vals = vals[np.isfinite(vals)]
        parts = ax.violinplot([vals], showmeans=True, showmedians=True)
        for b in parts.get("bodies", []):
            b.set_facecolor(palette[0])
            b.set_alpha(0.7)

    def _pie(self, ax, data, y_name, label_name, palette, donut, request):
        ycol = y_name or next(iter(data.keys()))
        require_columns(data, ycol)
        sizes = as_float(data[ycol])
        labels = None
        if label_name and label_name in data:
            labels = [str(v) for v in data[label_name]]
        elif request.get("labels"):
            labels = list(request["labels"])
        colors = [palette[i % len(palette)] for i in range(len(sizes))]
        ax.pie(
            sizes, labels=labels, colors=colors,
            autopct=request.get("autopct", "%1.1f%%"),
            startangle=request.get("startangle", 90),
            pctdistance=0.75 if donut else 0.6,
        )
        if donut:
            centre = plt.Circle((0, 0), 0.55, fc=ax.figure.get_facecolor())
            ax.add_artist(centre)
        ax.set_aspect("equal")

    def _heatmap(self, ax, data, request):
        tick_labels = request.get("tick_labels")
        if "z" in data:
            z = np.asarray(data["z"], dtype=float)
            if z.ndim == 1:
                n = int(np.sqrt(len(z)))
                if n * n != len(z):
                    raise GraphingError("E_DATA", "z must be 2D or square-length 1D")
                z = z.reshape(n, n)
        else:
            cols = {k: as_float(v) for k, v in data.items()}
            keys = list(cols)
            mat = np.column_stack([cols[k] for k in keys])
            if mat.shape[0] == mat.shape[1] and request.get("as_matrix"):
                z = mat
            else:
                z = np.corrcoef(mat, rowvar=False)
                tick_labels = keys
        cmap = request.get("cmap") or CMAPS["diverging"]
        vmin, vmax = request.get("vmin"), request.get("vmax")
        norm = Normalize(vmin=vmin, vmax=vmax) if vmin is not None or vmax is not None else None
        im = ax.imshow(z, cmap=cmap, aspect="auto", norm=norm)
        ax.figure.colorbar(im, ax=ax)
        if tick_labels and len(tick_labels) == z.shape[0]:
            ax.set_xticks(range(len(tick_labels)))
            ax.set_yticks(range(len(tick_labels)))
            ax.set_xticklabels(tick_labels, rotation=45, ha="right")
            ax.set_yticklabels(tick_labels)

    def _contour(self, ax, data, request):
        if not all(k in data for k in ("x", "y", "z")):
            raise GraphingError("E_DATA", "contour requires x, y, z", hint="z should be 2D")
        x = as_float(data["x"])
        y = as_float(data["y"])
        z = np.asarray(data["z"], dtype=float)
        if z.ndim != 2:
            raise GraphingError("E_DATA", "z must be 2D for contour")
        cs = ax.contourf(
            x, y, z, levels=int(request.get("levels") or 12),
            cmap=request.get("cmap") or CMAPS["sequential"],
        )
        ax.figure.colorbar(cs, ax=ax)
