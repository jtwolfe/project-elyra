"""Full-reset path clears (disk only).

Scope: absolute-path-guarded helpers that wipe ephemeral runtime product under
ElyraPaths (moments, messages, conversations, goals, wakes files, sandbox
contents, tool drafts, optional local tools).
In scope: path validation under data_dir / tools_dir; recreate empty dirs;
empty goals.json / messages; clear data/conversations/ (KD9, with messages);
never touch skills/local, identity, users, continuous.json, bundled
tools/skills, or model paths.
Out of scope: worker lock protocol, TimerService/WakeQueue memory, HTTP, Glass.
  Caller (PresenceWorker.reset_runtime_state) must hold exclusion.

Normative: full reset never deletes skills/local (K11). There is no
clear_local_skills flag.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths
from elyra.messages import MESSAGES_FILENAME
from elyra.tools.registry import drafts_dir

_LOG = logging.getLogger(__name__)


def _assert_under(path: Path, root: Path, *, label: str) -> Path:
    """Resolve ``path`` and require it is ``root`` or a descendant.

    Raises ValueError if path escapes root (symlink-aware via resolve).
    """
    root_r = root.resolve()
    path_r = path.resolve()
    if path_r != root_r and not path_r.is_relative_to(root_r):
        raise ValueError(f"{label} escapes root: {path_r} not under {root_r}")
    return path_r


def _clear_dir_contents(dir_path: Path) -> int:
    """Remove all entries under ``dir_path``; keep the directory. Return count."""
    if not dir_path.is_dir():
        dir_path.mkdir(parents=True, exist_ok=True)
        return 0
    n = 0
    for child in list(dir_path.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
        n += 1
    return n


def clear_moments(paths: ElyraPaths) -> dict[str, Any]:
    """Delete moment tapes + index; recreate empty ``data/moments/``."""
    moments = _assert_under(
        paths.data_dir / "moments", paths.data_dir, label="moments"
    )
    removed = 0
    if moments.is_dir():
        for child in list(moments.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
            removed += 1
    moments.mkdir(parents=True, exist_ok=True)
    # Empty index so list_moments is immediately empty (optional; missing ok).
    index = moments / "index.jsonl"
    index.write_text("", encoding="utf-8")
    return {"step": "moments", "removed": removed}


def clear_messages(paths: ElyraPaths) -> dict[str, Any]:
    """Unlink or truncate ``data/messages.jsonl``."""
    msg = _assert_under(
        paths.data_dir / MESSAGES_FILENAME, paths.data_dir, label="messages"
    )
    existed = msg.is_file()
    if existed:
        msg.unlink()
    # Recreate empty so append_message does not need ensure for missing parent.
    msg.parent.mkdir(parents=True, exist_ok=True)
    msg.write_text("", encoding="utf-8")
    return {"step": "messages", "existed": existed}


def clear_conversations(paths: ElyraPaths) -> dict[str, Any]:
    """Wipe ``data/conversations/`` (KD9 — social state with messages).

    Removes index + by_id records; recreates empty layout so ensure_layout is
    a no-op after reset. Identity/users are preserved (not under this tree).
    """
    root = _assert_under(
        paths.data_dir / "conversations", paths.data_dir, label="conversations"
    )
    removed = 0
    if root.is_dir():
        removed = _clear_dir_contents(root)
    # Re-scaffold empty layout (index + by_id).
    root.mkdir(parents=True, exist_ok=True)
    by_id = root / "by_id"
    by_id.mkdir(parents=True, exist_ok=True)
    index = root / "index.json"
    index.write_text(
        json.dumps(
            {"schema_version": 1, "conversations": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"step": "conversations", "removed": removed}


def clear_media(paths: ElyraPaths) -> dict[str, Any]:
    """Wipe ``data/media/**`` and sandbox media projection (KD13 / KD22).

    Full reset clears media with messages so glass refs never point at retained
    private blobs. Also clears ``sandboxes/sandbox0/media/**`` (keep dir).
    """
    media = _assert_under(
        paths.data_dir / "media", paths.data_dir, label="media"
    )
    removed = 0
    if media.is_dir():
        removed = _clear_dir_contents(media)
    # Re-scaffold empty layout for subsequent puts.
    from elyra.media.store import ensure_media_dirs

    ensure_media_dirs(paths)
    # Disposable RO mirror (host projection); keep media/ dir.
    try:
        from elyra.media.project import clear_sandbox_media

        removed += clear_sandbox_media(paths)
    except OSError as exc:
        _LOG.warning("clear_sandbox_media failed: %s", exc)
    return {"step": "media", "removed": removed}


def clear_goals(paths: ElyraPaths) -> dict[str, Any]:
    """Write ``data/goals/goals.json`` = ``{"goals": []}``."""
    goals_dir = _assert_under(
        paths.data_dir / "goals", paths.data_dir, label="goals"
    )
    goals_dir.mkdir(parents=True, exist_ok=True)
    store = goals_dir / "goals.json"
    store.write_text(
        json.dumps({"goals": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"step": "goals"}


def clear_wakes_disk(paths: ElyraPaths) -> dict[str, Any]:
    """Truncate/empty wakes event log + timers.json + waits.json on disk.

    Memory reload is the caller's job (WakeQueue / TimerService).
    """
    wakes = _assert_under(
        paths.data_dir / "wakes", paths.data_dir, label="wakes"
    )
    wakes.mkdir(parents=True, exist_ok=True)
    events = wakes / "events.jsonl"
    timers = wakes / "timers.json"
    waits = wakes / "waits.json"
    events.write_text("", encoding="utf-8")
    timers.write_text("[]\n", encoding="utf-8")
    waits.write_text("[]\n", encoding="utf-8")
    return {"step": "wakes", "files": ["events.jsonl", "timers.json", "waits.json"]}


def clear_sandbox(paths: ElyraPaths) -> dict[str, Any]:
    """Clear legacy ``data/sandbox/**`` and new-tree RW / projection dirs.

    Product FS root is ``sandboxes/sandbox0`` (H2c). Reset clears:
    - legacy ``data/sandbox/**`` (full wipe of contents)
    - ``sandboxes/sandbox0/{tmp,tools,media}`` (RW + media projection; RO seed
      lib/general/fixtures kept; never wipe seed without re-seed)

    Does **not** stop/remove the MSB instance (stop-only on process shutdown).
    """
    legacy = _assert_under(
        paths.data_dir / "sandbox", paths.data_dir, label="sandbox"
    )
    n_legacy = _clear_dir_contents(legacy)

    from elyra.sandbox.workspace_seed import (
        ensure_primary_sandbox_tree,
        host_primary_root,
    )

    primary = host_primary_root(paths)
    n_rw = 0
    # Guard: primary must stay under home.
    if primary.exists() or primary.parent.exists():
        primary_r = _assert_under(primary, paths.home, label="sandbox0")
        for sub in ("tmp", "tools", "media"):
            d = primary_r / sub
            if d.is_dir():
                n_rw += _clear_dir_contents(d)
            else:
                d.mkdir(parents=True, exist_ok=True)
        # Drop host-only pyenv marker so next warm reinstalls curated env.
        from elyra.sandbox.pyenv import clear_pyenv_marker

        if clear_pyenv_marker(primary_r):
            n_rw += 1
    # Re-scaffold seed + chmod so FS tools see a usable tree after reset.
    ensure_primary_sandbox_tree(paths)
    return {
        "step": "sandbox",
        "removed": n_legacy + n_rw,
        "legacy_removed": n_legacy,
        "rw_removed": n_rw,
    }


def clear_tool_drafts(paths: ElyraPaths) -> dict[str, Any]:
    """Clear ``tools/drafts/*`` packages; keep drafts root; never skills."""
    drafts = _assert_under(
        drafts_dir(paths), paths.tools_dir, label="drafts"
    )
    n = _clear_dir_contents(drafts)
    return {"step": "drafts", "removed": n}


def clear_local_tools(paths: ElyraPaths) -> dict[str, Any]:
    """Clear ``tools/local/*`` promoted tools (opt-in only; default preserve)."""
    local = _assert_under(
        paths.tools_dir / "local", paths.tools_dir, label="local_tools"
    )
    n = _clear_dir_contents(local)
    return {"step": "local_tools", "removed": n}


def ensure_preserved_dirs(paths: ElyraPaths) -> None:
    """Ensure dirs that must survive reset still exist (identity, users, …).

    Note: ``conversations/`` is **not** preserved — cleared with messages (KD9).
    Re-scaffolded empty by ``clear_conversations`` / callers after wipe.
    """
    for name in (
        "moments",
        "wakes",
        "identity",
        "users",
        "goals",
        "sandbox",
        "runtime",
        "media",
        "conversations",
    ):
        (paths.data_dir / name).mkdir(parents=True, exist_ok=True)
    from elyra.media.store import ensure_media_dirs

    ensure_media_dirs(paths)
    (paths.skills_dir / "local").mkdir(parents=True, exist_ok=True)
    (paths.tools_dir / "local").mkdir(parents=True, exist_ok=True)
    (paths.tools_dir / "drafts").mkdir(parents=True, exist_ok=True)


# Default flags for full reset (design F).
# ``reseed_self_if_default`` is intentionally not accepted until implemented.
DEFAULT_RESET_FLAGS: dict[str, bool] = {
    "clear_sandbox": True,
    "clear_drafts": True,
    "clear_local_tools": False,
}

# Accepted by API/normalize only — unknown keys ignored with warning.
_UNSUPPORTED_RESET_FLAGS = frozenset(
    {
        "clear_local_skills",  # always preserve skills/local (K11)
        "reseed_self_if_default",  # not implemented; reject until follow-up
    }
)


def normalize_reset_flags(raw: dict[str, Any] | None) -> dict[str, bool]:
    """Merge caller flags with defaults; ignore unknown / unsupported keys."""
    out = dict(DEFAULT_RESET_FLAGS)
    if not raw:
        return out
    for key in DEFAULT_RESET_FLAGS:
        if key in raw:
            out[key] = bool(raw[key])
    for bad in _UNSUPPORTED_RESET_FLAGS:
        if raw.get(bad):
            _LOG.warning(
                "reset flag %s requested but ignored (unsupported / always-preserve)",
                bad,
            )
    return out
