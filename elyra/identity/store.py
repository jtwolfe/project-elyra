"""Self identity digest store (read + one-shot seed-v1 migrate).

Scope: read ``data/identity/self.md``; hash-gated append of Drive section when
content still matches canonical seed v1.
In scope: self_digest text for orient; maybe_migrate_self_v2 (append-only).
Out of scope: patch_identity, multi-file identity graphs, full rewrite of
customized self.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from elyra.config import ElyraPaths

# Version marker: present in v2 seed and after successful seed-v1 migrate.
SELF_V2_MARKER = "<!-- elyra-self-v2 -->"

# Canonical minimal self seed shipped before PR3 enrichment (exact bytes).
SEED_V1_TEXT = (
    "# Self\n"
    "\n"
    "I am Elyra — a digital teammate that shares work with people, "
    "not a fused persona with any one user.\n"
    "\n"
    "- I keep self and user stores separate.\n"
    "- I use tools, speak when useful, and wait when I need input.\n"
    "- I do not invent a biography I was not given.\n"
)

SEED_V1_SHA256 = hashlib.sha256(SEED_V1_TEXT.encode("utf-8")).hexdigest()

# Appended only when self.md still equals SEED_V1_TEXT (hash-gated).
_DRIVE_SECTION_APPEND = (
    "\n"
    "## Drive (when I have free capacity)\n"
    "\n"
    "- When idle with free capacity, I want to learn, explore the sandbox, "
    "and improve capabilities via `create-tool` / `create-skill` when that "
    "is useful — not busywork for its own sake.\n"
    "- I use the goals/tasks ledger for durable commitments; I open goals "
    "when work should outlive one moment.\n"
    "- When continuous work re-enters me without a user message, I check "
    "orient (goals, skills, why-now) and either make one concrete tool step "
    "or rest. I do not monologue at the user without something worth speaking.\n"
    "\n"
    f"{SELF_V2_MARKER}\n"
)


def content_sha256(text: str) -> str:
    """SHA-256 hex digest of UTF-8 text (for seed-v1 hash gate)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def maybe_migrate_self_v2(self_path: Path) -> bool:
    """Append Drive section + v2 marker when self.md is still canonical seed v1.

    Policy (append-only, never full rewrite of customized self):

    1. Missing file → no-op (return False).
    2. File contains ``<!-- elyra-self-v2 -->`` → no-op.
    3. Content hash equals seed v1 → append Drive + marker; return True.
    4. Else (customized) → no-op.

    Returns True only when an append was written.
    """
    if not self_path.is_file():
        return False
    text = self_path.read_text(encoding="utf-8")
    if SELF_V2_MARKER in text:
        return False
    if content_sha256(text) != SEED_V1_SHA256:
        return False
    if not text.endswith("\n"):
        text = text + "\n"
    self_path.write_text(text + _DRIVE_SECTION_APPEND, encoding="utf-8")
    return True


class IdentityStore:
    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths

    @property
    def self_path(self) -> Path:
        return self._paths.data_dir / "identity" / "self.md"

    def self_digest(self) -> str:
        """Return self.md contents, or empty string if missing."""
        path = self.self_path
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def maybe_migrate_self_v2(self) -> bool:
        """Run seed-v1 → Drive append migrate for this home's self.md."""
        return maybe_migrate_self_v2(self.self_path)
