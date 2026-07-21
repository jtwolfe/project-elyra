"""Per-user profile digest store (read-only in Stretch 1).

Scope: read ``data/users/<id>/profile.md``.
In scope: profile text for orient (at most one user per wake); user_id jail.
Out of scope: patch_user, cross-user inject, fused self+user files.
"""

from __future__ import annotations

import re
from pathlib import Path

from elyra.config import ElyraPaths

# Single segment: letter/digit start; alnum, dot, underscore, hyphen after.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_user_id(user_id: str) -> str:
    """Return ``user_id`` if it is a single safe path segment.

    Requires ``^[A-Za-z0-9][A-Za-z0-9._-]*$`` so blank/whitespace, control
    chars (including NUL), separators, and ``.`` / ``..`` are rejected.
    """
    if not isinstance(user_id, str) or not _USER_ID_RE.fullmatch(user_id):
        raise ValueError(f"invalid user_id: {user_id!r}")
    # Defense in depth: path parts must still be exactly one segment.
    path = Path(user_id)
    if path.is_absolute() or len(path.parts) != 1:
        raise ValueError(f"invalid user_id: {user_id!r}")
    return user_id


class UsersStore:
    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths

    def profile_path(self, user_id: str) -> Path:
        """Path to ``data/users/<id>/profile.md`` (user_id must be a single segment)."""
        safe_id = _validate_user_id(user_id)
        users_root = (self._paths.data_dir / "users").resolve()
        path = (users_root / safe_id / "profile.md").resolve()
        if not path.is_relative_to(users_root):
            raise ValueError(f"invalid user_id: {user_id!r}")
        return path

    def profile(self, user_id: str) -> str:
        """Return profile.md for ``user_id``, or empty string if missing.

        Raises ValueError for unsafe ``user_id`` (path-jail).
        """
        path = self.profile_path(user_id)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
