"""High-level plot() and quick() APIs."""

from __future__ import annotations

import ast
import math
from typing import Any

from .errors import GraphingError
from .types import PlotResult


def _try_import_mpl():
    try:
        from .backends.mpl_backend import MplBackend  # noqa: WPS433
        return MplBackend
    except Exception:  # noqa: BLE001
        return None


def _get_backend(name: str | None):
    key = (name or "auto").lower()
    if key == "auto":
        Mpl = _try_import_mpl()
        if Mpl is not None:
            return Mpl()
        from .backends.svg_backend import SvgBackend
        return SvgBackend()
    if key in {"svg", "stdlib", "stdlib_svg"}:
        from .backends.svg_backend import SvgBackend
        return SvgBackend()
    if key in {"mpl", "matplotlib"}:
        Mpl = _try_import_mpl()
        if Mpl is None:
            raise GraphingError(
                "E_BACKEND",
                "matplotlib backend unavailable",
                hint="numpy/matplotlib not installed; use backend=\'svg\' or backend=\'auto\'",
            )
        return Mpl()
    if key == "plotly":
        from .backends.plotly_backend import PlotlyBackend
        return PlotlyBackend()
    if key == "altair":
        from .backends.altair_backend import AltairBackend
        return AltairBackend()
    raise GraphingError("E_BACKEND", f"backend {key!r} unavailable", hint="auto|svg|mpl")


def plot(
    kind: str = "line",
    data: Any = None,
    *,
    x: str | None = None,
    y: str | None = None,
    color: str | None = None,
    out: str,
    backend: str = "auto",
    **kwargs: Any,
) -> PlotResult:
    """Render a chart to `out` and return PlotResult."""
    request = {
        "kind": kind,
        "data": data,
        "x": x,
        "y": y,
        "color": color,
        "out": out,
        "backend": backend,
        **kwargs,
    }
    return _get_backend(backend).render(request)


_ALLOWED_FUNCS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "fabs": math.fabs,
}

_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.BoolOp, ast.And, ast.Or, ast.IfExp,
)


class _SafeVisitor(ast.NodeVisitor):
    def generic_visit(self, node):
        if not isinstance(node, _ALLOWED_NODES):
            raise GraphingError("E_EXPR", f"disallowed expression node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise GraphingError("E_EXPR", "only simple function calls allowed")
        if node.func.id not in _ALLOWED_FUNCS:
            raise GraphingError("E_EXPR", f"function not allowed: {node.func.id}")
        if node.keywords:
            raise GraphingError("E_EXPR", "keyword args not allowed in expressions")
        for a in node.args:
            self.visit(a)

    def visit_Name(self, node: ast.Name):
        if node.id in {"x", "t"} or node.id in _ALLOWED_FUNCS or node.id in _ALLOWED_CONSTS:
            return
        raise GraphingError("E_EXPR", f"name not allowed: {node.id}")


def _eval_expr_scalar(expr: str, x: float) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise GraphingError("E_EXPR", "invalid expression syntax", hint=str(e)) from e
    _SafeVisitor().visit(tree)
    env = {"x": x, "t": x, **_ALLOWED_FUNCS, **_ALLOWED_CONSTS}
    try:
        return float(eval(compile(tree, "<quick>", "eval"), {"__builtins__": {}}, env))  # noqa: S307
    except Exception as e:  # noqa: BLE001
        raise GraphingError("E_EXPR", "expression evaluation failed", hint=str(e)) from e


def quick(
    expr: str,
    *,
    x: tuple[float, float] = (0.0, 10.0),
    n: int = 500,
    out: str = "tmp/plots/quick.svg",
    **kwargs: Any,
) -> PlotResult:
    """Plot y=f(x) for a safe math expression."""
    if n < 2 or n > 1_000_000:
        raise GraphingError("E_SCHEMA", "n out of range")
    x0, x1 = float(x[0]), float(x[1])
    if n == 1:
        xs = [x0]
    else:
        step = (x1 - x0) / (n - 1)
        xs = [x0 + i * step for i in range(n)]
    ys = [_eval_expr_scalar(expr, xv) for xv in xs]
    title = kwargs.pop("title", expr)
    return plot(
        kind=kwargs.pop("kind", "line"),
        data={"x": xs, "y": ys},
        x="x",
        y="y",
        out=out,
        title=title,
        xlabel=kwargs.pop("xlabel", "x"),
        ylabel=kwargs.pop("ylabel", "y"),
        **kwargs,
    )
