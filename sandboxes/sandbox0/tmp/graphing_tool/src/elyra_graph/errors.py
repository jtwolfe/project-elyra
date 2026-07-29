"""Typed errors for the graphing toolkit."""

from __future__ import annotations


class GraphingError(Exception):
    """Structured graphing failure."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"[{code}] {message}" + (f" — {hint}" if hint else ""))
