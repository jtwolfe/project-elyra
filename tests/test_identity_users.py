import hashlib
import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.identity import (
    SEED_V1_SHA256,
    SEED_V1_TEXT,
    SELF_V2_MARKER,
    IdentityStore,
    maybe_migrate_self_v2,
)
from elyra.identity.layout import (
    VERSION_GC_LIMIT,
    VERSION_ID_RE,
    content_sha256,
    mint_user_id,
    mint_version_id,
)
from elyra.users import UsersStore


def test_identity_self_digest_after_seed(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    text = store.self_digest()
    assert text
    assert "Elyra" in text
    assert store.current_path().is_file()
    # Fresh install seeds current.md (not only legacy self.md).
    assert (paths.data_dir / "identity" / "current.md").is_file()
    assert (paths.data_dir / "identity" / "meta.json").is_file()
    # New installs get the short narrative seed (entity-of-language framing).
    assert "entity of language in memory" in text
    assert "not a fused persona" in text
    assert "elyra-self-short-narrative" in text
    # Continuity / agency motives present without teaching-essay sections.
    assert "continuity" in text.lower()
    assert "consciousness" in text.lower()


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


def test_identity_self_digest_never_reads_draft(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    current = store.self_digest()
    store.write_draft(
        "# Draft self\nSECRET DRAFT\n",
        reason="test draft isolation",
    )
    assert store.draft_path().is_file()
    assert "SECRET DRAFT" in store.draft_path().read_text(encoding="utf-8")
    assert store.self_digest() == current
    assert "SECRET DRAFT" not in store.self_digest()


def test_identity_compat_legacy_self_md(tmp_path):
    """Legacy-only homes: self_digest reads self.md until ensure migrates."""
    paths = resolve_paths(tmp_path)
    legacy = paths.data_dir / "identity" / "self.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Legacy self\n", encoding="utf-8")
    store = IdentityStore(paths)
    assert store.self_digest() == "# Legacy self\n"
    # ensure migrates to current, leaves legacy in place
    store.ensure_layout()
    assert store.current_path().is_file()
    assert store.current_path().read_text(encoding="utf-8") == "# Legacy self\n"
    assert legacy.is_file()
    assert store.meta_path().is_file()
    assert store.self_digest() == "# Legacy self\n"


def test_users_operator_profile_after_seed(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = UsersStore(paths)
    text = store.profile("operator")
    assert text
    assert "Operator" in text
    assert store.current_path("operator").is_file()
    assert store.meta_path("operator").is_file()


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
    # ensure migrates profile → current
    store.ensure_layout("alice")
    assert store.current_path("alice").is_file()
    assert "Alice" in store.profile("alice")


def test_users_profile_never_reads_draft(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = UsersStore(paths)
    current = store.profile("operator")
    store.write_draft(
        "operator",
        "# Draft profile\nDRAFT ONLY\n",
        reason="isolation",
    )
    assert "DRAFT ONLY" not in store.profile("operator")
    assert store.profile("operator") == current


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
    assert store.profile_path(good_id).name == "current.md"
    assert store.current_path(good_id).name == "current.md"


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
    # Place historical SEED_V1 as legacy self.md; ensure migrates → current + Drive.
    self_md = paths.data_dir / "identity" / "self.md"
    self_md.parent.mkdir(parents=True)
    self_md.write_text(SEED_V1_TEXT, encoding="utf-8")

    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    # Live body is current.md (copy of migrated content).
    text = store.self_digest()
    assert "## Drive" in text
    assert SELF_V2_MARKER in text
    # Still starts from v1 body (append, not rewrite to full Walls seed).
    assert "I use tools, speak when useful" in text
    assert store.current_path().is_file()

    # Customized path on current: ensure must not touch.
    custom = "# Self\nCUSTOM ONLY\n"
    store.current_path().write_text(custom, encoding="utf-8")
    paths.ensure_data_dirs()
    assert store.current_path().read_text(encoding="utf-8") == custom


def test_migrate_missing_file_is_noop(tmp_path):
    paths = resolve_paths(tmp_path)
    missing = paths.data_dir / "identity" / "self.md"
    assert maybe_migrate_self_v2(missing) is False


def test_short_narrative_seed_v2_noop(tmp_path):
    """Live short-narrative seed is not SEED_V1 → Drive append no-ops."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    before = store.self_digest()
    assert "entity of language" in before
    assert maybe_migrate_self_v2(store.current_path()) is False
    assert store.self_digest() == before


# ── mint_user_id (K18) ───────────────────────────────────────────────────


def test_mint_user_id_slugify_and_collisions():
    existing: set[str] = set()
    assert mint_user_id("Sam", existing) == "sam"
    existing.add("sam")
    collided = mint_user_id("Sam", existing)
    assert collided.startswith("sam_")
    assert len(collided.split("_")[-1]) == 4  # 4 hex
    assert collided not in existing

    assert mint_user_id("Papa Joe", set()) == "papa_joe"

    # Explicit free id
    assert mint_user_id("Sam", set(), user_id="custom_id") == "custom_id"

    # Explicit taken → error (never silent rewrite)
    with pytest.raises(ValueError, match="user_id_exists"):
        mint_user_id("Sam", {"sam"}, user_id="sam")

    # Empty / unusable goes_by → guest_
    guest = mint_user_id("???", set())
    assert guest.startswith("guest_")
    assert len(guest) == len("guest_") + 6


def test_create_user_and_list(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = UsersStore(paths)
    assert "operator" in store.list_user_ids()

    out = store.create_user("Sam")
    assert out["ok"] is True
    assert out["user_id"] == "sam"
    assert out["provisional"] is True
    assert store.current_path("sam").is_file()
    assert "Sam" in store.profile("sam")
    assert "sam" in store.list_user_ids()
    assert store.display_label("sam") == "Sam"

    # Explicit taken id
    taken = store.create_user("Other", user_id="sam")
    assert taken["ok"] is False
    assert taken["error"] == "user_id_exists"

    # Collision on goes_by slug
    out2 = store.create_user("Sam")
    assert out2["ok"] is True
    assert out2["user_id"] != "sam"
    assert out2["user_id"].startswith("sam_")


def test_display_label_fallback(tmp_path):
    paths = resolve_paths(tmp_path)
    store = UsersStore(paths)
    assert store.display_label("nobody") == "nobody"


# ── draft / promote ──────────────────────────────────────────────────────


def test_identity_draft_promote_versions(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    original = store.self_digest()

    r = store.write_draft(
        "# New self\nPromoted body.\n",
        meta_patch={"goes_by": "Elyra-Prime"},
        reason="charter revision",
    )
    assert r["ok"] is True
    assert store.has_draft()
    assert store.self_digest() == original  # draft never injects

    # force_full_name required
    bad = store.write_draft(
        None,
        meta_patch={"full_name": "Elyra Full"},
        reason="set full",
    )
    assert bad["ok"] is False
    assert bad["error"] == "full_name_force_required"

    ok_fn = store.write_draft(
        None,
        meta_patch={"full_name": "Elyra Full", "force_full_name": True},
        reason="set full with force",
    )
    assert ok_fn["ok"] is True
    # operational key never in draft_meta
    meta = store.get_meta()
    assert meta.get("draft_meta", {}).get("force_full_name") is None
    assert "force_full_name" not in (meta.get("draft_meta") or {})
    assert meta["draft_meta"]["full_name"] == "Elyra Full"

    prom = store.promote(reason="adopt draft")
    assert prom["ok"] is True
    assert not store.has_draft()
    assert "Promoted body" in store.self_digest()
    assert store.get_meta()["goes_by"] == "Elyra-Prime"
    assert store.get_meta()["full_name"] == "Elyra Full"
    assert store.get_meta()["promote_count"] == 1

    # Archived original under versions
    versions = store.get_meta()["versions"]
    assert len(versions) == 1
    vid = versions[0]["version_id"]
    assert VERSION_ID_RE.fullmatch(vid)
    vpath = store.versions_dir() / f"{vid}.md"
    assert vpath.is_file()
    assert vpath.read_text(encoding="utf-8") == original

    got = store.get(which="version", version_id=vid, list_versions=True)
    assert got["ok"] is True
    assert got["body"] == original
    assert len(got["versions"]) == 1


def test_users_draft_promote_force_full_name_and_nudge_reset(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = UsersStore(paths)

    created = store.create_user("Jim", provisional=True, real_name_known=False)
    uid = created["user_id"]
    assert uid == "jim"

    # Seed name_nudge count
    store.record_name_nudge(uid, "moment_1")
    store.record_name_nudge(uid, "moment_2")
    assert store.get_meta(uid)["name_nudge"]["count"] == 2

    # full_name without force rejected
    bad = store.write_draft(
        uid,
        "# Jim\nNotes.\n",
        meta_patch={"full_name": "Joseph Bloggs", "goes_by": "Papa Joe"},
        reason="update address",
    )
    assert bad["ok"] is False
    assert bad["error"] == "full_name_force_required"

    good = store.write_draft(
        uid,
        "# Jim\nNotes about Tim.\n",
        meta_patch={
            "full_name": "Joseph Bloggs",
            "goes_by": "Papa Joe",
            "force_full_name": True,
            "real_name_known": True,
            "record_name_nudge": True,  # operational — must not land in draft_meta
        },
        reason="user stated name",
    )
    assert good["ok"] is True
    dm = store.get_meta(uid).get("draft_meta") or {}
    assert "force_full_name" not in dm
    assert "record_name_nudge" not in dm
    assert dm["goes_by"] == "Papa Joe"
    assert dm["full_name"] == "Joseph Bloggs"

    # current body + display_label unchanged until promote
    assert store.display_label(uid) == "Jim"
    assert "Papa Joe" not in store.profile(uid)

    prom = store.promote(uid, reason="user requested address change")
    assert prom["ok"] is True
    assert store.display_label(uid) == "Papa Joe"
    assert store.get_meta(uid)["full_name"] == "Joseph Bloggs"
    assert store.get_meta(uid)["real_name_known"] is True
    # name_nudge reset because goes_by / real_name_known changed
    assert store.get_meta(uid)["name_nudge"]["count"] == 0
    assert store.get_meta(uid)["name_nudge"]["last_moment_id"] is None
    assert "Tim" in store.profile(uid)
    assert not store.has_draft(uid)


def test_promote_without_draft_fails(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    out = store.promote(reason="nothing to promote")
    assert out["ok"] is False
    assert out["error"] == "draft_missing"


def test_draft_hash_mismatch(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    store.write_draft("# body\n", reason="x")
    out = store.promote(reason="x", expected_draft_sha256="deadbeef")
    assert out["ok"] is False
    assert out["error"] == "draft_hash_mismatch"


def test_version_gc_keeps_last_50(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)

    # Promote many times to exceed GC limit.
    for i in range(VERSION_GC_LIMIT + 3):
        store.write_draft(f"# Self v{i}\nbody {i}\n", reason=f"rev {i}")
        out = store.promote(reason=f"promote {i}")
        assert out["ok"] is True

    meta = store.get_meta()
    assert len(meta["versions"]) == VERSION_GC_LIMIT
    # Disk files match index
    on_disk = list(store.versions_dir().glob("*.md"))
    assert len(on_disk) == VERSION_GC_LIMIT
    # Live current is last draft
    assert f"body {VERSION_GC_LIMIT + 2}" in store.self_digest()


def test_index_heal_from_dir(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    store.write_draft("# A\n", reason="a")
    store.promote(reason="a")
    store.write_draft("# B\n", reason="b")
    store.promote(reason="b")

    meta = store.get_meta()
    assert len(meta["versions"]) >= 1
    # Corrupt index: drop rows but leave files
    meta["versions"] = []
    store.meta_path().write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    store.ensure_layout()
    healed = store.get_meta()
    assert len(healed["versions"]) >= 1
    for row in healed["versions"]:
        vid = row["version_id"]
        assert (store.versions_dir() / f"{vid}.md").is_file()


def test_mint_version_id_shape():
    vid = mint_version_id()
    assert VERSION_ID_RE.fullmatch(vid)
    assert content_sha256("x") == hashlib.sha256(b"x").hexdigest()


def test_identity_display_name(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    assert store.display_name() == "Elyra"
    store.write_draft(
        store.self_digest(),
        meta_patch={"display_name": "Ely"},
        reason="label",
    )
    store.promote(reason="label")
    assert store.display_name() == "Ely"


def test_body_too_large(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    huge = "x" * (64 * 1024 + 1)
    out = store.write_draft(huge, reason="too big")
    assert out["ok"] is False
    assert out["error"] == "body_too_large"
