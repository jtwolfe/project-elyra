"""Persistent sandbox FS and process runner.

Scope: one jail under ``sandboxes/sandbox0`` (host tree); text FS ops +
shell=False run. Guest-style ``/workspace`` paths normalize to the root.
In scope: read/write/list/grep/search_replace, stream-capped run with timeout.
Out of scope: network isolation, cgroups, multi-sandbox, tool registry,
container/namespace FS isolation for ``run``.

Trust boundary
--------------
- **FS methods** (``read_text``, ``write_text``, ``list_dir``, ``grep``,
  ``search_replace``) are path-jailed under
  ``$ELYRA_HOME/sandboxes/sandbox0/``. Symlink targets outside the root are
  denied. Hard links to outside inodes (same UID) are a known path-jail
  limitation (not mount isolation).
- **``run`` is process-level only**: same UID as Elyra, ``cwd`` pinned to the
  sandbox root, scrubbed env (no host secret inherit), ``shell=False``.
  It is **not** a chroot, container, or seccomp jail. Child argv can open
  absolute host paths and use the network. Local-operator trust boundary;
  do not treat ``run`` as host-escape prevention until OS isolation exists.
- Timeout kill is process-group scoped (``start_new_session`` + killpg). A
  child that ``setsid`` / double-forks into a new session can outlive killpg
  without cgroups (S1 residual).
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    PathEscapeError,
    ensure_host_tree,
    resolve,
)

DEFAULT_RUN_TIMEOUT_SECONDS = 60
OUTPUT_CAP_BYTES = 256 * 1024  # 256 KiB retained per stream
# After killpg, bound how long we wait for pipes/process to finish.
_POST_KILL_DRAIN_SECONDS = 2.0
_PIPE_READ_CHUNK = 65_536

# Minimal env for child processes: no host secrets (no API keys, tokens, etc.).
_MINIMAL_PATH = "/usr/bin:/bin:/usr/local/bin"

# Keys/prefixes that must not be injected via optional ``env=`` (loader/hijack).
_BLOCKED_ENV_KEYS = frozenset(
    {
        "HOME",
        "BASH_ENV",
        "ENV",
        "IFS",
        "SHELLOPTS",
        "PS4",
        "PROMPT_COMMAND",
        "SSLKEYLOGFILE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
    }
)
_BLOCKED_ENV_PREFIXES = (
    "LD_",
    "DYLD_",
    "PYTHON",
    "PERL",
    "BASH_",
    "RUBYOPT",
    "NODE_OPTIONS",
)


@dataclass(frozen=True)
class RunResult:
    """Outcome of a sandbox ``run``."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    argv: tuple[str, ...] = ()


def normalize_user_path(user_path: str) -> str:
    """Map guest-style ``/workspace`` prefixes to sandbox-relative paths.

    Models often pass guest paths after seeing docs. Absolute paths that are
    *not* under ``/workspace`` are left unchanged (path jail still rejects
    host escapes).
    """
    if not isinstance(user_path, str):
        return user_path
    if user_path == GUEST_WORKSPACE_ROOT or user_path == f"{GUEST_WORKSPACE_ROOT}/":
        return "."
    prefix = f"{GUEST_WORKSPACE_ROOT}/"
    if user_path.startswith(prefix):
        rest = user_path[len(prefix) :]
        return rest if rest else "."
    return user_path


class Sandbox:
    """One persistent workspace under ``$ELYRA_HOME/sandboxes/sandbox0/``."""

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        # Product FS root (H2c cutover): host tree for sandbox0, not data/sandbox.
        # Ensure seed + RW scaffold on construct so FS tools never see a missing root.
        self._root = ensure_host_tree(PRIMARY_NAME, paths)

    @property
    def root(self) -> Path:
        return self._root

    def ensure_root(self) -> Path:
        """Ensure host tree (seed + RW dirs); return resolved root."""
        self._root = ensure_host_tree(PRIMARY_NAME, self._paths)
        return self._root

    def resolve(self, user_path: str) -> Path:
        """Resolve a user path inside the jail (see ``paths.resolve``).

        Accepts guest-style absolute prefixes (``/workspace``, ``/workspace/...``)
        and normalizes them to sandbox-relative before the path jail.
        """
        self.ensure_root()
        return resolve(self._root, normalize_user_path(user_path))

    # --- mutability (L2 host path policy; media/ only in v1 — KD7) ---

    def is_readonly_relpath(self, user_path: str) -> bool:
        """True if ``user_path`` resolves under ``media/`` (chat media projection).

        v1: **media/ only**. Do not treat lib/general/fixtures as host-tool
        write-denied — guest MSB already RO-binds those; host-stub may still
        write seed dirs (existing dogfood/tests). Broaden only with an explicit
        product decision.
        """
        self.ensure_root()
        norm = normalize_user_path(user_path)
        try:
            candidate = resolve(self._root, norm)
        except (PathEscapeError, ValueError, TypeError):
            return False
        media_root = (self._root / "media").resolve()
        try:
            candidate.relative_to(media_root)
            return True
        except ValueError:
            return False

    # Design alias (normative name in multimodal design doc).
    is_media_protected_relpath = is_readonly_relpath

    def assert_mutable(self, user_path: str) -> None:
        """Raise ``PermissionError("media_readonly")`` if path is media-protected."""
        if self.is_readonly_relpath(user_path):
            raise PermissionError("media_readonly")

    # --- FS ops ---

    def read_text(self, user_path: str, *, encoding: str = "utf-8") -> str:
        """Read a text file under the sandbox.

        Path-jailed only (see module trust boundary). Uncapped file size in S1.
        Raises ``IsADirectoryError`` when the path is a directory;
        ``FileNotFoundError`` when missing or not a regular file.
        """
        norm = normalize_user_path(user_path)
        path = self.resolve(user_path)
        # Re-check immediately before open (mitigate resolve→use TOCTOU).
        path = resolve(self._root, norm)
        if path.is_dir():
            raise IsADirectoryError(f"is a directory: {user_path!r}")
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {user_path!r}")
        return path.read_text(encoding=encoding)

    def write_text(
        self,
        user_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        make_parents: bool = True,
    ) -> Path:
        """Write text to a path under the sandbox (creates parents by default).

        Denies writes under ``media/`` (``PermissionError("media_readonly")``).
        """
        self.assert_mutable(user_path)
        norm = normalize_user_path(user_path)
        path = self.resolve(user_path)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Parent must stay inside jail (mkdir could not escape via resolve).
            try:
                path.parent.resolve().relative_to(self._root)
            except ValueError as exc:
                raise PathEscapeError(
                    f"parent escapes sandbox: {user_path!r}"
                ) from exc
        # Re-resolve before write (symlink swap between check and use).
        path = resolve(self._root, norm)
        path.write_text(content, encoding=encoding)
        return path

    def list_dir(self, user_path: str = ".") -> list[str]:
        """List directory entry names (not recursive). ``.`` is sandbox root.

        Raises ``FileNotFoundError`` when the path does not exist;
        ``NotADirectoryError`` when it exists but is not a directory.
        """
        path = self.resolve(user_path)
        if not path.exists():
            raise FileNotFoundError(f"not found: {user_path!r}")
        if not path.is_dir():
            raise NotADirectoryError(f"not a directory: {user_path!r}")
        return sorted(p.name for p in path.iterdir())

    def grep(
        self,
        pattern: str,
        user_path: str = ".",
        *,
        regex: bool = False,
        max_matches: int = 200,
    ) -> list[dict[str, object]]:
        """Simple content search under a path (files only; recursive).

        Streams each file line-by-line (no full-file buffer). Returns list of
        ``{path, line, text}`` (path relative to sandbox root).
        """
        base = self.resolve(user_path)
        if base.is_file():
            files = [base]
        elif base.is_dir():
            files = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            raise FileNotFoundError(f"not found: {user_path!r}")

        matcher = re.compile(pattern) if regex else None
        hits: list[dict[str, object]] = []
        for fpath in files:
            # Skip anything that escaped (symlink race); re-check under root.
            try:
                resolved = fpath.resolve()
                resolved.relative_to(self._root)
            except ValueError:
                continue
            rel = str(resolved.relative_to(self._root))
            try:
                with fpath.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, start=1):
                        # strip only the trailing newline for display parity
                        text = line.rstrip("\n\r")
                        if matcher is not None:
                            if not matcher.search(text):
                                continue
                        elif pattern not in text:
                            continue
                        hits.append({"path": rel, "line": i, "text": text})
                        if len(hits) >= max_matches:
                            return hits
            except OSError:
                continue
        return hits

    def search_replace(
        self,
        user_path: str,
        old: str,
        new: str,
        *,
        count: int = 0,
        encoding: str = "utf-8",
    ) -> int:
        """Replace ``old`` with ``new`` in a file; atomic enough (temp + replace).

        ``count`` is passed to ``str.replace`` (0 = all). Returns number of
        replacements performed. Denies mutation under ``media/``.
        """
        if not old:
            raise ValueError("old must be non-empty")
        self.assert_mutable(user_path)
        norm = normalize_user_path(user_path)
        path = self.resolve(user_path)
        path = resolve(self._root, norm)
        if path.is_dir():
            raise IsADirectoryError(f"is a directory: {user_path!r}")
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {user_path!r}")
        original = path.read_text(encoding=encoding)
        if count == 0:
            replaced = original.replace(old, new)
            n = original.count(old)
        else:
            replaced = original.replace(old, new, count)
            n = min(count, original.count(old))
        if n == 0:
            return 0
        self._atomic_write_text(path, replaced, encoding=encoding)
        return n

    def _atomic_write_text(
        self, path: Path, content: str, *, encoding: str
    ) -> None:
        """Write via temp file in the same directory then os.replace.

        Defense-in-depth: deny if ``path`` resolves under ``media/``.
        """
        try:
            rel = path.resolve().relative_to(self._root.resolve()).as_posix()
            self.assert_mutable(rel)
        except (ValueError, TypeError):
            # Path outside root should not reach here; fail closed on media only.
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # --- process run ---

    def run(
        self,
        command: str | Sequence[str],
        *,
        timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
        output_cap: int = OUTPUT_CAP_BYTES,
    ) -> RunResult:
        """Run a command with shell=False, cwd=sandbox, scrubbed env.

        Prefer an argv list. If ``command`` is a string, it is split with
        ``shlex.split`` (never passed to a shell). Timeout kills the process
        group (best-effort). stdout/stderr are read in chunks and **retained**
        only up to ``output_cap`` bytes each (excess discarded while still
        draining pipes).

        Trust: process-level only (cwd + env + shell=False). Not a container;
        child code can touch host FS/network. See module docstring.
        """
        if output_cap < 0:
            raise ValueError(f"output_cap must be >= 0, got {output_cap!r}")
        self.ensure_root()
        argv = self._normalize_argv(command)
        child_env = self._scrubbed_env(env)

        # start_new_session → new process group; killpg on timeout.
        proc = subprocess.Popen(
            argv,
            shell=False,
            cwd=str(self._root),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        raw_out, raw_err, out_trunc, err_trunc, timed_out = _collect_capped(
            proc,
            timeout=timeout,
            output_cap=output_cap,
        )
        returncode = proc.returncode if proc.returncode is not None else -1
        if timed_out and returncode == 0:
            returncode = -1

        return RunResult(
            returncode=returncode,
            stdout=raw_out.decode("utf-8", errors="replace"),
            stderr=raw_err.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            stdout_truncated=out_trunc,
            stderr_truncated=err_trunc,
            argv=tuple(argv),
        )

    @staticmethod
    def _normalize_argv(command: str | Sequence[str]) -> list[str]:
        if isinstance(command, str):
            argv = shlex.split(command)
            if not argv:
                raise ValueError("empty command string")
            return argv
        argv = [str(a) for a in command]
        if not argv:
            raise ValueError("empty argv")
        return argv

    def _scrubbed_env(
        self, extra: Mapping[str, str] | None
    ) -> dict[str, str]:
        """Minimal environment: PATH + HOME=sandbox; no host secrets.

        Host env is never merged. Optional ``extra`` may override ``PATH``
        only (e.g. verify_tool); dangerous loader/interpreter keys are dropped.
        """
        env: dict[str, str] = {
            "PATH": _MINIMAL_PATH,
            "HOME": str(self._root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TERM": "dumb",
        }
        if not extra:
            return env
        for k, v in extra.items():
            key = str(k)
            upper = key.upper()
            if upper == "PATH":
                # Intentional override for controlled callers (e.g. verify).
                env["PATH"] = str(v)
                continue
            if _is_blocked_env_key(upper):
                continue
            env[key] = str(v)
        return env


def _is_blocked_env_key(upper_key: str) -> bool:
    if upper_key in _BLOCKED_ENV_KEYS:
        return True
    return any(upper_key.startswith(p) for p in _BLOCKED_ENV_PREFIXES)


def _collect_capped(
    proc: subprocess.Popen[bytes],
    *,
    timeout: float,
    output_cap: int,
) -> tuple[bytes, bytes, bool, bool, bool]:
    """Wait for ``proc`` with stream caps; never retain more than cap per stream.

    Reads stdout/stderr on background threads in chunks. Excess bytes are
    discarded (still drained so writers do not block). On timeout: killpg,
    then drain with a bounded post-kill wait — never unbounded ``communicate``.
    """
    out_buf = bytearray()
    err_buf = bytearray()
    out_trunc = False
    err_trunc = False
    trunc_lock = threading.Lock()

    def reader(stream: IO[bytes] | None, buf: bytearray, which: str) -> None:
        nonlocal out_trunc, err_trunc
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(_PIPE_READ_CHUNK)
                if not chunk:
                    break
                with trunc_lock:
                    room = output_cap - len(buf)
                    if room > 0:
                        buf.extend(chunk[:room])
                        if len(chunk) > room:
                            if which == "out":
                                out_trunc = True
                            else:
                                err_trunc = True
                    else:
                        if which == "out":
                            out_trunc = True
                        else:
                            err_trunc = True
                    # Excess: intentionally not retained (still loop to drain).
        except ValueError:
            # Pipe closed while reading.
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    t_out = threading.Thread(
        target=reader, args=(proc.stdout, out_buf, "out"), daemon=True
    )
    t_err = threading.Thread(
        target=reader, args=(proc.stderr, err_buf, "err"), daemon=True
    )
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        try:
            proc.wait(timeout=_POST_KILL_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            # Stuck descendant or uninterruptible state: force-close pipes so
            # reader threads unblock; never await without a deadline.
            _force_close_pipes(proc)
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=_POST_KILL_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                pass

    # Readers should exit after EOF/close; bound the join.
    t_out.join(timeout=_POST_KILL_DRAIN_SECONDS)
    t_err.join(timeout=_POST_KILL_DRAIN_SECONDS)
    if t_out.is_alive() or t_err.is_alive():
        _force_close_pipes(proc)
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

    return bytes(out_buf), bytes(err_buf), out_trunc, err_trunc, timed_out


def _force_close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the child's process group; best-effort (not tree-wide if setsid)."""
    if proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
