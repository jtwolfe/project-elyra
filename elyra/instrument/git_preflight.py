"""Git preflight helpers for grok_build (base branch existence).

Scope: resolve working/base branch for execute_plan preflight.
Out of scope: network push/PR.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def branch_exists(repo: Path, name: str) -> bool:
    """True if local or origin/<name> exists."""
    repo = Path(repo)
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        return True
    r2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"origin/{name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r2.returncode == 0
