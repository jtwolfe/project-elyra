import pytest

from elyra.config import resolve_paths
from elyra.identity import IdentityStore
from elyra.users import UsersStore


def test_identity_self_digest_after_seed(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store = IdentityStore(paths)
    text = store.self_digest()
    assert text
    assert "Elyra" in text
    assert store.self_path.is_file()


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
    ["..", ".", "", "../x", "a/b", "a\\b", "/etc", "~root"],
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
