"""Central path resolution.

Scope: ELYRA_HOME and conventional directories; seed copy on first run.
In scope: home override, model/data/skills/tools/prompts paths, ensure_data_dirs.
Out of scope: feature flags, secrets, settings.toml, runtime.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "ELYRA_HOME"

# Relative seed paths under prompts/
_SEED_SELF = Path("seeds") / "identity" / "self.md"
_SEED_OPERATOR = Path("seeds") / "users" / "operator" / "profile.md"


def project_root() -> Path:
    """Repo / install root (parent of the elyra package)."""
    return Path(__file__).resolve().parent.parent


def _detect_project_root() -> Path:
    return project_root()


@dataclass(frozen=True)
class ElyraPaths:
    home: Path
    model_dir: Path
    data_dir: Path
    skills_dir: Path
    tools_dir: Path
    prompts_dir: Path

    def ensure_data_dirs(self) -> None:
        """Create runtime dirs and seed digests once (never overwrite)."""
        for name in ("moments", "wakes", "identity", "users", "goals", "sandbox"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

        for path in (
            self.skills_dir / "local",
            self.tools_dir / "local",
            self.tools_dir / "drafts",
        ):
            path.mkdir(parents=True, exist_ok=True)

        self._seed_if_missing(
            dest=self.data_dir / "identity" / "self.md",
            seed_rel=_SEED_SELF,
        )
        self._seed_if_missing(
            dest=self.data_dir / "users" / "operator" / "profile.md",
            seed_rel=_SEED_OPERATOR,
        )

    def resolve_seed(self, seed_rel: Path | str) -> Path | None:
        """Locate a seed template: home prompts first, then project-root prompts."""
        rel = Path(seed_rel)
        for base in (self.prompts_dir, project_root() / "prompts"):
            candidate = base / rel
            if candidate.is_file():
                return candidate
        return None

    def _seed_if_missing(self, dest: Path, seed_rel: Path) -> None:
        if dest.exists():
            return
        src = self.resolve_seed(seed_rel)
        if src is None:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


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
