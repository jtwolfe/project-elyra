"""Persistent sandbox FS and process runner.

Scope: one jail under data/sandbox/; text FS ops + shell=False run.
In scope: read/write/list/grep/search_replace, capped run with timeout.
Out of scope: network isolation, cgroups, multi-sandbox, tool registry.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.sandbox.paths import PathEscapeError, resolve

DEFAULT_RUN_TIMEOUT_SECONDS = 60
OUTPUT_CAP_BYTES = 256 * 1024  # 256 KiB per stream

# Minimal env for child processes: no host secrets (no API keys, tokens, etc.).
_MINIMAL_PATH = "/usr/bin:/bin:/usr/local/bin"


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


class Sandbox:
    """One persistent workspace under ``$ELYRA_HOME/data/sandbox/``."""

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        self._root = (paths.data_dir / "sandbox").resolve()

    @property
    def root(self) -> Path:
        return self._root

    def ensure_root(self) -> Path:
        """Create the sandbox directory if missing; return resolved root."""
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def resolve(self, user_path: str) -> Path:
        """Resolve a user path inside the jail (see ``paths.resolve``)."""
        self.ensure_root()
        return resolve(self._root, user_path)

    # --- FS ops ---

    def read_text(self, user_path: str, *, encoding: str = "utf-8") -> str:
        """Read a text file under the sandbox."""
        path = self.resolve(user_path)
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
        """Write text to a path under the sandbox (creates parents by default)."""
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
        path.write_text(content, encoding=encoding)
        return path

    def list_dir(self, user_path: str = ".") -> list[str]:
        """List directory entry names (not recursive). ``.`` is sandbox root."""
        path = self.resolve(user_path)
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

        Returns list of ``{path, line, text}`` (path relative to sandbox root).
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
                fpath.resolve().relative_to(self._root)
            except ValueError:
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(fpath.resolve().relative_to(self._root))
            for i, line in enumerate(text.splitlines(), start=1):
                if matcher is not None:
                    if not matcher.search(line):
                        continue
                elif pattern not in line:
                    continue
                hits.append({"path": rel, "line": i, "text": line})
                if len(hits) >= max_matches:
                    return hits
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
        replacements performed.
        """
        if not old:
            raise ValueError("old must be non-empty")
        path = self.resolve(user_path)
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
        """Write via temp file in the same directory then os.replace."""
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
        """Run a command inside the sandbox with shell=False.

        Prefer an argv list. If ``command`` is a string, it is split with
        ``shlex.split`` (never passed to a shell). Timeout kills the process
        group. stdout/stderr are capped at ``output_cap`` bytes each.
        """
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
        timed_out = False
        try:
            raw_out, raw_err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
            raw_out, raw_err = proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else -1
        if timed_out and returncode == 0:
            returncode = -1

        stdout, out_trunc = _cap_bytes(raw_out or b"", output_cap)
        stderr, err_trunc = _cap_bytes(raw_err or b"", output_cap)
        return RunResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
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
        """Minimal environment: PATH + HOME=sandbox; no host secrets."""
        env: dict[str, str] = {
            "PATH": _MINIMAL_PATH,
            "HOME": str(self._root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TERM": "dumb",
        }
        # Preserve locale-ish only; never copy API keys / tokens from host.
        if extra:
            for k, v in extra.items():
                if k.upper() in ("PATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
                    # Allow PATH override only via explicit extra if needed;
                    # still never inherit host PATH by default.
                    if k.upper() == "PATH":
                        env["PATH"] = v
                    continue
                env[str(k)] = str(v)
        return env


def _cap_bytes(data: bytes, cap: int) -> tuple[str, bool]:
    truncated = len(data) > cap
    if truncated:
        data = data[:cap]
    text = data.decode("utf-8", errors="replace")
    return text, truncated


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the child's process group; best-effort on all platforms."""
    if proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
