"""Shared package content hashing for promote gates and guest stage skip.

Single implementation used by verify/promote and guest_exec stage gate
(KD-G5). Kept free of guest_exec imports to avoid the verify↔guest_exec cycle.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Files excluded from package content hash (promote + stage gate).
# Stage copy also ignores other names (``__pycache__``, marker); those remain
# in the hash intentionally — see design-guest-package-stage-reliability §1.
VERIFY_RECORD_NAME = ".verify.json"
CONTENT_HASH_EXCLUDE_NAMES = frozenset({VERIFY_RECORD_NAME})


def content_hash(package_dir: Path) -> str:
    """SHA-256 over sorted ``(relpath, bytes)`` excluding ``.verify.json``.

    Paths use POSIX separators relative to ``package_dir``. Directories are
    not hashed; only regular files participate.
    """
    package_dir = Path(package_dir)
    entries: list[tuple[str, Path]] = []
    if not package_dir.is_dir():
        return hashlib.sha256().hexdigest()
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir).as_posix()
        # Exclude verify sidecar anywhere named .verify.json
        if path.name == VERIFY_RECORD_NAME or rel == VERIFY_RECORD_NAME:
            continue
        if path.name in CONTENT_HASH_EXCLUDE_NAMES:
            continue
        entries.append((rel, path))
    digest = hashlib.sha256()
    for rel, path in sorted(entries, key=lambda item: item[0]):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "CONTENT_HASH_EXCLUDE_NAMES",
    "VERIFY_RECORD_NAME",
    "content_hash",
]
