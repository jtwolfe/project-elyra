"""Sandbox0 host tree seed — layout, ensure, seed copy for primary sandbox.

Scope: primary sandbox0 root resolution, ensure host tree, repo seed copy.
In scope: ``{ELYRA_HOME}/sandboxes/sandbox0`` layout + seed from repo.
Out of scope: microsandbox guest ensure SM, Lance bootstrap.

Product ``Sandbox`` FS root is this host tree (H2c cutover).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path

from elyra.config import ElyraPaths, project_root, resolve_paths

_LOG = logging.getLogger(__name__)

PRIMARY_INSTANCE = "sandbox0"
NEW_ROOT_REL = Path("sandboxes") / PRIMARY_INSTANCE

# Seed entries copied into run dirs / primary tree (dirs or files).
SEED_ENTRIES = ("lib", "general", "fixtures", "README.md")
# Always present under primary host root (RW in guest when isolation on;
# media is RO guest bind but still ensured empty on host — KD22).
PRIMARY_ALWAYS_DIRS = ("lib", "general", "fixtures", "media", "tmp", "tools")
# Dirs excluded from workspace_snapshot hash (mutable or projection churn).
_SNAPSHOT_EXCLUDE_TOP = frozenset({"tmp", "tools", "media"})


def host_primary_root(paths: ElyraPaths | None = None) -> Path:
    """Return ``{ELYRA_HOME}/sandboxes/sandbox0`` (may not exist yet)."""
    layout = paths or resolve_paths()
    return layout.home / NEW_ROOT_REL


def has_general_seed(root: Path) -> bool:
    """True when root has at least one general/*.py seed tool."""
    general = root / "general"
    return general.is_dir() and any(general.glob("*.py"))


def primary_sandbox_root(paths: ElyraPaths | None = None) -> Path:
    """Return primary host root ``sandboxes/sandbox0`` (may be empty until ensure)."""
    return host_primary_root(paths).resolve()


def repo_seed_source() -> Path | None:
    """Locate the repo-shipped ``sandboxes/sandbox0`` seed tree, if present."""
    candidate = project_root() / NEW_ROOT_REL
    if candidate.is_dir():
        return candidate
    return None


def ensure_primary_sandbox_tree(
    paths: ElyraPaths | None = None,
    *,
    seed_source: Path | None = None,
) -> Path:
    """Ensure sandboxes/sandbox0 exists with seed layout.

    Copies missing seed entries from ``seed_source``, else the repo seed at
    ``project_root()/sandboxes/sandbox0`` when present, else leaves empty
    scaffold dirs only. Never merges legacy ``data/sandbox/`` (PR3 cutover).

    Returns resolved primary root. Best-effort chmod is applied after seed;
    partial chmod failure does not leave the tree half-created (dirs already
    exist; seed copy is non-destructive by default).
    """
    layout = paths or resolve_paths()
    dest = host_primary_root(layout)
    dest.mkdir(parents=True, exist_ok=True)
    for name in PRIMARY_ALWAYS_DIRS:
        (dest / name).mkdir(exist_ok=True)

    source = seed_source
    if source is None:
        source = repo_seed_source()

    if source is not None and source.is_dir():
        # Refuse to copy a tree onto itself (no-op seed when home == project root).
        try:
            if source.resolve() != dest.resolve():
                _copy_seed_entries(source, dest, overwrite=False)
        except OSError as exc:
            _LOG.warning("seed copy failed for %s → %s: %s", source, dest, exc)

    _apply_host_chmod_policy(dest)
    return dest.resolve()


def ensure_host_tree(
    paths: ElyraPaths | None = None,
    *,
    seed_source: Path | None = None,
) -> Path:
    """Alias for :func:`ensure_primary_sandbox_tree` (primary sandbox0 only)."""
    return ensure_primary_sandbox_tree(paths, seed_source=seed_source)


# Always refresh operator-reviewed allowlist so product trees track repo seed.
# Do NOT always-refresh requirements-curated.txt: sandbox_pip_update mutates
# the product curated file; re-seeding it on every ensure would wipe adds.
# Curated still copies on first seed when missing (default copytree/child path).
_ALWAYS_REFRESH_SEED_FILES = frozenset(
    {
        "lib/requirements-allowlist.txt",
    }
)


def _copy_seed_entries(source: Path, dest: Path, *, overwrite: bool) -> None:
    for entry in SEED_ENTRIES:
        src = source / entry
        if not src.exists():
            continue
        target = dest / entry
        if src.is_dir():
            if overwrite and target.exists():
                shutil.rmtree(target)
            if not target.exists():
                shutil.copytree(
                    src,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                for child in src.iterdir():
                    if child.name == "__pycache__" or child.suffix == ".pyc":
                        continue
                    out = target / child.name
                    rel = f"{entry}/{child.name}".replace("\\", "/")
                    force = rel in _ALWAYS_REFRESH_SEED_FILES
                    if out.exists() and not overwrite and not force:
                        continue
                    if child.is_dir():
                        if out.exists() and (overwrite or force):
                            shutil.rmtree(out)
                        if not out.exists():
                            shutil.copytree(
                                child,
                                out,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                            )
                    else:
                        shutil.copy2(child, out)
        else:
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def _apply_host_chmod_policy(root: Path) -> None:
    """Best-effort host modes matching DESIGN mount expectations."""
    try:
        for name in ("lib", "general", "fixtures"):
            path = root / name
            if path.is_dir():
                path.chmod(0o755)
                for child in path.rglob("*"):
                    if child.is_dir():
                        child.chmod(0o755)
                    elif child.is_file():
                        child.chmod(0o644)
        # media/: host projection dir 0o755; projected files set 0o444 at project time.
        media = root / "media"
        if media.is_dir():
            media.chmod(0o755)
        tmp = root / "tmp"
        if tmp.is_dir():
            tmp.chmod(0o1777)
        tools = root / "tools"
        if tools.is_dir():
            tools.chmod(0o755)
    except OSError as exc:
        _LOG.debug("chmod policy partial failure under %s: %s", root, exc)


def seed_run_workspace(
    *,
    paths: ElyraPaths | None = None,
    dest: Path | None = None,
) -> Path:
    """Copy sandbox seed entries into a run temp dir for execution."""
    layout = paths or resolve_paths()
    source = ensure_primary_sandbox_tree(layout)
    run_dir = dest or Path(tempfile.mkdtemp(prefix="elyra-sandbox-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    _copy_seed_entries(source, run_dir, overwrite=True)
    (run_dir / "tmp").mkdir(exist_ok=True)
    return run_dir


def _snapshot_files(root: Path) -> list[Path]:
    """Files that participate in workspace_snapshot (seed only; not RW tmp/tools)."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel_parts = path.relative_to(root).parts
        if rel_parts and rel_parts[0] in _SNAPSHOT_EXCLUDE_TOP:
            continue
        files.append(path)
    return sorted(files)


def workspace_snapshot_hash(
    *,
    paths: ElyraPaths | None = None,
    workspace_root: Path | None = None,
) -> str:
    """Hash sandbox workspace seed used for execution workspace_snapshot field.

    Excludes ``tmp/``, ``tools/``, and ``media/`` so RW content and attachment
    projection churn do not skew the audit hash (KD22).
    """
    root = workspace_root or primary_sandbox_root(paths)
    if not root.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in _snapshot_files(root):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
