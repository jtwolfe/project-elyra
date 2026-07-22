import hashlib

import pytest

from elyra.config import resolve_paths
from elyra.identity import (
    SEED_V1_SHA256,
    SEED_V1_TEXT,
    SELF_V2_MARKER,
    IdentityStore,
    maybe_migrate_self_v2,
)
from elyra.users import UsersStore


def test_identity_self_digest_after_seed(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    text = store.self_digest()
    assert text
    assert "Elyra" in text
    assert store.self_path.is_file()
    # New installs get enriched seed with v2 marker (no migrate needed).
    assert SELF_V2_MARKER in text
    assert "## Drive" in text
    assert "## Walls" in text
    # self ≠ user walls present
    assert "User prefs never go into self" in text


def test_seed_v1_hash_is_stable():
    """Gate must match the historical minimal seed bytes exactly."""
    expected = hashlib.sha256(SEED_V1_TEXT.encode("utf-8")).hexdigest()
    assert SEED_V1_SHA256 == expected
    assert SEED_V1_SHA256 == (
        "9ae61af01754c02fff9c887b0ad0516ff1a410e5a49411afa4cfd28569962266"
    )


def test_identity_missing_file_returns_empty(tmp_path):
    paths = resolve_paths(tmp_path)
    # do not seed
    store = IdentityStore(paths)
    assert store.self_digest() == ""


def test_users_operator_profile_after_seed(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = UsersStore(paths)
    text = store.profile("operator")
    assert text
    assert "Operator" in text


def test_users_missing_profile_returns_empty(tmp_path):
    paths = resolve_paths(tmp_path)
    store = UsersStore(paths)
    assert store.profile("operator") == ""
    assert store.profile("unknown") == ""


def test_users_custom_profile_read(tmp_path):
    paths = resolve_paths(tmp_path)
    dest = paths.data_dir / "users" / "alice" / "profile.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("# Alice\nPrefers brief notes.\n", encoding="utf-8")
    store = UsersStore(paths)
    assert "Alice" in store.profile("alice")


@pytest.mark.parametrize(
    "bad_id",
    [
        "..",
        ".",
        "",
        " ",
        "\t",
        "\n",
        "op\x00er",
        "a b",
        "../x",
        "a/b",
        "a\\b",
        "/etc",
        "~root",
        "-leading-hyphen",
        ".dotstart",
        "_understart",
    ],
)
def test_users_profile_rejects_path_escape(tmp_path, bad_id):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    # plant a file that a naive join might read
    (paths.data_dir / "profile.md").write_text("ESCAPED\n", encoding="utf-8")
    store = UsersStore(paths)
    with pytest.raises(ValueError, match="invalid user_id"):
        store.profile(bad_id)
    with pytest.raises(ValueError, match="invalid user_id"):
        store.profile_path(bad_id)


@pytest.mark.parametrize("good_id", ["operator", "alice", "u1", "A_B-2.3"])
def test_users_profile_accepts_conservative_ids(tmp_path, good_id):
    paths = resolve_paths(tmp_path)
    store = UsersStore(paths)
    # path builds without error; missing file still returns ""
    assert store.profile(good_id) == ""
    assert store.profile_path(good_id).name == "profile.md"


def test_migrate_seed_v1_appends_drive_and_marker(tmp_path):
    """Canonical seed v1 → append Drive section + <!-- elyra-self-v2 -->."""
    paths = resolve_paths(tmp_path)
    self_md = paths.data_dir / "identity" / "self.md"
    self_md.parent.mkdir(parents=True)
    self_md.write_text(SEED_V1_TEXT, encoding="utf-8")

    assert maybe_migrate_self_v2(self_md) is True
    text = self_md.read_text(encoding="utf-8")
    # Original v1 body preserved (append-only, not full rewrite).
    assert text.startswith(SEED_V1_TEXT.rstrip("\n"))
    assert "## Drive (when I have free capacity)" in text
    assert SELF_V2_MARKER in text
    assert "create-tool" in text
    # Idempotent: second call no-ops.
    assert maybe_migrate_self_v2(self_md) is False
    assert self_md.read_text(encoding="utf-8") == text


def test_migrate_customized_self_is_noop(tmp_path):
    """Customized self (hash ≠ seed v1, no marker) must not be rewritten."""
    paths = resolve_paths(tmp_path)
    self_md = paths.data_dir / "identity" / "self.md"
    self_md.parent.mkdir(parents=True)
    custom = "# Self\n\nI am a customized teammate.\n"
    self_md.write_text(custom, encoding="utf-8")

    assert maybe_migrate_self_v2(self_md) is False
    assert self_md.read_text(encoding="utf-8") == custom


def test_migrate_already_marked_is_noop(tmp_path):
    """File with v2 marker is never re-appended even if Drive is missing."""
    paths = resolve_paths(tmp_path)
    self_md = paths.data_dir / "identity" / "self.md"
    self_md.parent.mkdir(parents=True)
    marked = f"# Self\n\nMinimal.\n\n{SELF_V2_MARKER}\n"
    self_md.write_text(marked, encoding="utf-8")

    assert maybe_migrate_self_v2(self_md) is False
    assert self_md.read_text(encoding="utf-8") == marked


def test_ensure_data_dirs_migrates_seed_v1_only(tmp_path):
    """ensure_data_dirs runs migrate: seed v1 gets Drive; custom stays put."""
    paths = resolve_paths(tmp_path)
    self_md = paths.data_dir / "identity" / "self.md"
    self_md.parent.mkdir(parents=True)
    self_md.write_text(SEED_V1_TEXT, encoding="utf-8")

    paths.ensure_data_dirs()
    text = self_md.read_text(encoding="utf-8")
    assert "## Drive" in text
    assert SELF_V2_MARKER in text
    # Still starts from v1 body (append, not rewrite to full Walls seed).
    assert "I use tools, speak when useful" in text

    # Customized path: ensure must not touch.
    custom = "# Self\nCUSTOM ONLY\n"
    self_md.write_text(custom, encoding="utf-8")
    paths.ensure_data_dirs()
    assert self_md.read_text(encoding="utf-8") == custom


def test_migrate_missing_file_is_noop(tmp_path):
    paths = resolve_paths(tmp_path)
    missing = paths.data_dir / "identity" / "self.md"
    assert maybe_migrate_self_v2(missing) is False
