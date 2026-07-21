"""Central path resolution.

Scope: ELYRA_HOME and conventional directories.
In scope: home override, model/data/skills/tools paths.
Out of scope: feature flags, secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "ELYRA_HOME"


def _detect_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ElyraPaths:
    home: Path
    model_dir: Path
    data_dir: Path
    skills_dir: Path
    tools_dir: Path
    prompts_dir: Path

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "moments").mkdir(exist_ok=True)
        (self.data_dir / "wakes").mkdir(exist_ok=True)


def resolve_home(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_HOME, "").strip()
    if env:
        return Path(os.path.expanduser(env)).resolve()
    return _detect_project_root()


def resolve_paths(home: Path | str | None = None) -> ElyraPaths:
    root = resolve_home(home)
    return ElyraPaths(
        home=root,
        model_dir=root / "model",
        data_dir=root / "data",
        skills_dir=root / "skills",
        tools_dir=root / "tools",
        prompts_dir=root / "prompts",
    )
