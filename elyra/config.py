"""Central path resolution.

Scope: ELYRA_HOME and conventional directories; seed copy on first run.
In scope: home override, model/data/skills/tools/prompts paths, ensure_data_dirs
(including ``data/secrets`` for API key store, ``data/runtime`` prefs, and
``data/browser`` for optional Playwright session data).
Out of scope: feature flags, settings.toml, secret *values* (see ``elyra.llm.auth``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "ELYRA_HOME"

def project_root() -> Path:
    """Repo / install root (parent of the elyra package)."""
    return Path(__file__).resolve().parent.parent


def _is_safe_relative(rel: Path) -> bool:
    """True if rel is a non-empty relative path with no ``.`` / ``..`` segments."""
    if rel.is_absolute() or not rel.parts:
        return False
    return all(part not in ("", ".", "..") for part in rel.parts)


@dataclass(frozen=True)
class ElyraPaths:
    home: Path
    model_dir: Path
    data_dir: Path
    skills_dir: Path
    tools_dir: Path
    prompts_dir: Path

    def ensure_data_dirs(self) -> None:
        """Create runtime dirs; seed/migrate identity + users layouts.

        Seeds write ``current.md`` (not only legacy ``self.md`` / ``profile.md``).
        ``IdentityStore.ensure_layout`` / ``UsersStore.ensure_layout`` run the
        normative migrate order (legacy → current, meta.json, seed-v1 Drive
        append on resolved live path, versions index heal). Customized digests
        and already-marked v2 self files are left untouched.

        Missing seed templates are a quiet no-op (dirs are still created) so a
        tmp ELYRA_HOME without repo prompts does not hard-fail ensure; packaging
        mistakes surface later as empty digests from the stores.
        """
        for name in (
            "moments",
            "wakes",
            "identity",
            "users",
            "goals",
            "sandbox",
            "runtime",  # continuous.json + provider.json + usage.json
            "media",  # content-addressed attachments (KD1)
            "browser",  # optional Playwright session user-data (v1 may use temp)
        ):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

        # Media store subdirs + lightweight reconcile/GC (PR10 / KD23).
        from elyra.media.gc import ensure_media

        ensure_media(self)

        # Secrets store (mode 0700): xai_api_key (llm.auth) + named values/ (PR5).
        secrets = self.data_dir / "secrets"
        secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(secrets, 0o700)
        except OSError:
            pass
        values = secrets / "values"
        values.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(values, 0o700)
        except OSError:
            pass

        for path in (
            self.skills_dir / "local",
            self.tools_dir / "local",
            self.tools_dir / "drafts",
        ):
            path.mkdir(parents=True, exist_ok=True)

        # Prefer current.md destinations; do not re-seed legacy if current exists.
        # Stores own full ensure/migrate (seed, legacy copy, meta, v2 append, heal).
        # Lazy import avoids config↔identity/users cycle at module load.
        from elyra.identity.store import IdentityStore
        from elyra.users.store import UsersStore

        IdentityStore(self).ensure_layout()
        UsersStore(self).ensure_layout()

    def resolve_seed(self, seed_rel: Path | str) -> Path | None:
        """Locate a seed template: home prompts first, then project-root prompts.

        Rejects absolute paths and any ``.`` / ``..`` segment. Returns None when
        the relative path is unsafe or no template file exists.
        """
        rel = Path(seed_rel)
        if not _is_safe_relative(rel):
            return None
        for base in (self.prompts_dir, project_root() / "prompts"):
            base_resolved = base.resolve()
            candidate = (base / rel).resolve()
            try:
                if not candidate.is_relative_to(base_resolved):
                    continue
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                return candidate
        return None

    def _seed_if_missing(self, dest: Path, seed_rel: Path) -> None:
        """Copy seed to dest only when dest is absent.

        Raises FileExistsError if dest exists but is not a regular file (e.g. a
        directory blocking the digest path). If no seed source is found, returns
        without error (quiet no-op; see ensure_data_dirs docstring).
        """
        if dest.exists():
            if not dest.is_file():
                raise FileExistsError(
                    f"seed dest exists but is not a file: {dest}"
                )
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
    return project_root()


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
