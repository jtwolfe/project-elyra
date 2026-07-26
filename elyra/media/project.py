"""Sandbox RO media projection (PR2 / KD7).

Host-truth blobs live under ``data/media/blobs/``. This module mirrors an
attachment into ``sandboxes/sandbox0/media/<att_id>/<filename>`` so guest tools
can *see* chat media via the RO ``/workspace/media`` mount.

Projection is privileged host I/O (bypasses Sandbox ``assert_mutable``). Guest
and host FS tools still cannot mutate the mirror (RO bind + media_readonly).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.types import Attachment
from elyra.sandbox.workspace_seed import ensure_primary_sandbox_tree, host_primary_root

_LOG = logging.getLogger(__name__)

MEDIA_TOP = "media"
# Projected files are host-owned read-only mirrors (chmod after copy path).
_PROJECTED_FILE_MODE = 0o444
_MEDIA_DIR_MODE = 0o755


def sandbox_media_root(paths: ElyraPaths | None = None) -> Path:
    """Return ``sandboxes/sandbox0/media`` (ensures primary tree + media dir)."""
    layout = paths or resolve_paths()
    root = ensure_primary_sandbox_tree(layout)
    media = root / MEDIA_TOP
    media.mkdir(parents=True, exist_ok=True)
    try:
        media.chmod(_MEDIA_DIR_MODE)
    except OSError:
        pass
    return media


def projected_path_for(att: Attachment, *, paths: ElyraPaths | None = None) -> Path:
    """Absolute host path for the sandbox mirror of ``att``."""
    layout = paths or resolve_paths()
    rel = att.sandbox_relpath or f"{MEDIA_TOP}/{att.id}/{att.filename}"
    # Jail: only allow under media/<att_id>/...
    parts = Path(rel).parts
    if not parts or parts[0] != MEDIA_TOP:
        raise ValueError(f"sandbox_relpath must start with media/: {rel!r}")
    media = sandbox_media_root(layout)
    dest = (media / Path(*parts[1:])).resolve()
    if not dest.is_relative_to(media.resolve()):
        raise ValueError(f"sandbox_relpath escapes media/: {rel!r}")
    return dest


def project_attachment(
    att: Attachment,
    blob_path: Path,
    *,
    paths: ElyraPaths | None = None,
) -> Path:
    """Project ``blob_path`` into the sandbox media mirror for ``att``.

    Algorithm (design):
    1. Ensure ``media/<att_id>/``
    2. Try ``os.link(blob, dest)`` (same FS under ELYRA_HOME is usual)
    3. On ``OSError`` → ``shutil.copy2`` then ``chmod 0o444``
    4. Replace existing dest if present (re-project / reconcile)

    Returns the absolute destination path. Does not mutate attachment meta.
    """
    if not blob_path.is_file():
        raise FileNotFoundError(f"blob missing for projection: {blob_path}")
    dest = projected_path_for(att, paths=paths)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.parent.chmod(_MEDIA_DIR_MODE)
    except OSError:
        pass

    # Remove prior projection so link/copy can land cleanly.
    if dest.exists() or dest.is_symlink():
        try:
            dest.unlink()
        except OSError as exc:
            _LOG.debug("unlink prior projection %s: %s", dest, exc)

    linked = False
    try:
        os.link(blob_path, dest)
        linked = True
    except OSError as exc:
        _LOG.debug("hardlink failed %s → %s (%s); copying", blob_path, dest, exc)
        shutil.copy2(blob_path, dest)
        try:
            dest.chmod(_PROJECTED_FILE_MODE)
        except OSError:
            pass

    if linked:
        # Hardlinked file shares inode mode with blob; best-effort RO for tools.
        # Skip chmod on hardlink when it would alter the canonical blob mode —
        # only chmod when nlink == 1 (copy path already chmods).
        try:
            if dest.stat().st_nlink == 1:
                dest.chmod(_PROJECTED_FILE_MODE)
        except OSError:
            pass

    return dest


def clear_sandbox_media(paths: ElyraPaths | None = None) -> int:
    """Remove all entries under ``sandboxes/sandbox0/media/``; keep the dir.

    Returns number of top-level entries removed. Used by full reset (KD22).
    """
    layout = paths or resolve_paths()
    primary = host_primary_root(layout)
    media = primary / MEDIA_TOP
    if not media.is_dir():
        media.mkdir(parents=True, exist_ok=True)
        return 0
    n = 0
    for child in list(media.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
        n += 1
    try:
        media.chmod(_MEDIA_DIR_MODE)
    except OSError:
        pass
    return n
