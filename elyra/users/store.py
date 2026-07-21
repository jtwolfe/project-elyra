"""Per-user profile digest store (read-only in Stretch 1).

Scope: read ``data/users/<id>/profile.md``.
In scope: profile text for orient (at most one user per wake).
Out of scope: patch_user, cross-user inject, fused self+user files.
"""

from __future__ import annotations

from pathlib import Path

from elyra.config import ElyraPaths


class UsersStore:
    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths

    def profile_path(self, user_id: str) -> Path:
        return self._paths.data_dir / "users" / user_id / "profile.md"

    def profile(self, user_id: str) -> str:
        """Return profile.md for ``user_id``, or empty string if missing."""
        path = self.profile_path(user_id)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
