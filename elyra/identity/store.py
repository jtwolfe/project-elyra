"""Self identity digest store (read-only in Stretch 1).

Scope: read ``data/identity/self.md``.
In scope: self_digest text for orient.
Out of scope: patch_identity, multi-file identity graphs.
"""

from __future__ import annotations

from pathlib import Path

from elyra.config import ElyraPaths


class IdentityStore:
    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths

    @property
    def self_path(self) -> Path:
        return self._paths.data_dir / "identity" / "self.md"

    def self_digest(self) -> str:
        """Return self.md contents, or empty string if missing."""
        path = self.self_path
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
