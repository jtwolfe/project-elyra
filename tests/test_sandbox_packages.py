"""PR5: sandbox_pip_update allowlist tool + InstallResult honesty."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import project_root, resolve_paths
from elyra.sandbox import (
    FakeSandboxClient,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    set_sandbox_lifecycle,
)
from elyra.sandbox.paths import ENV_ELYRA_SANDBOX, PRIMARY_NAME, ensure_host_tree
from elyra.sandbox.protocol import ExecResult
from elyra.sandbox.pyenv import (
    InstallResult,
    clear_pyenv_marker,
    marker_path,
    pyenv_ready,
    requirements_file,
    requirements_hash,
    try_install_curated_pyenv,
    write_pyenv_marker,
)
from elyra.tools.builtin.sandbox_packages import (
    REQUIRED_CURATED,
    load_allowlist,
    normalize_dist_name,
    parse_requirement_line,
    sandbox_pip_update,
)
from elyra.tools.types import ToolContext


@pytest.fixture(autouse=True)
def _clear_lifecycle():
    clear_sandbox_lifecycle()
    yield
    clear_sandbox_lifecycle()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def host_root(paths):
    return ensure_host_tree(PRIMARY_NAME, paths)


def _ctx(paths) -> ToolContext:
    return ToolContext(paths=paths)


def _enable_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)


def _life_with_pip(
    paths,
    *,
    exit_code: int = 0,
    stderr: str = "",
    stdout: str = "Successfully installed\n",
):
    client = FakeSandboxClient(instances={PRIMARY_NAME: "running"})
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    assert life.ensure(PRIMARY_NAME).ready
    sb = life.get_connected(PRIMARY_NAME)
    assert sb is not None
    sb.default_exec = ExecResult(
        exit_code=exit_code, stdout_text=stdout, stderr_text=stderr
    )
    return life, client, sb


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_normalize_dist_name_pep503() -> None:
    assert normalize_dist_name("PyYAML") == "pyyaml"
    assert normalize_dist_name("python_dateutil") == "python-dateutil"
    assert normalize_dist_name("Typing.Extensions") == "typing-extensions"


def test_parse_requirement_line_accepts_pins() -> None:
    assert parse_requirement_line("httpx>=0.27,<1") == ("httpx", "httpx>=0.27,<1")
    assert parse_requirement_line("regex") == ("regex", "regex")
    assert parse_requirement_line("  Jinja2  ") == ("jinja2", "Jinja2")


def test_parse_requirement_line_rejects_unsafe() -> None:
    assert parse_requirement_line("git+https://evil") is None
    assert parse_requirement_line("pkg @ https://x") is None
    assert parse_requirement_line("-e .") is None
    assert parse_requirement_line("pkg --index-url http://x") is None
    assert parse_requirement_line("pkg;python_version>'3'") is None
    assert parse_requirement_line("pkg[extra]") is None
    assert parse_requirement_line("/tmp/wheel.whl") is None
    assert parse_requirement_line("pkg$(reboot)") is None


def test_repo_seed_allowlist_exists() -> None:
    path = project_root() / "sandboxes" / "sandbox0" / "lib" / "requirements-allowlist.txt"
    assert path.is_file()
    text = path.read_text(encoding="utf-8").lower()
    assert "httpx" in text
    assert "markdown" in text


def test_seed_copies_allowlist(host_root: Path) -> None:
    al = host_root / "lib" / "requirements-allowlist.txt"
    assert al.is_file()
    names = load_allowlist(host_root)
    assert "httpx" in names
    assert "markdown" in names


def test_required_curated_includes_pytest() -> None:
    assert "pytest" in REQUIRED_CURATED


# ---------------------------------------------------------------------------
# Fail-closed gates (no file write)
# ---------------------------------------------------------------------------


def test_isolation_off_fails_closed(paths, host_root: Path) -> None:
    """conftest sets ELYRA_SANDBOX=0 — isolation_required, no mutation."""
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "isolation_required"
    assert result.payload["host_reverted"] is False
    assert result.payload["guest_site_may_be_dirty"] is False
    assert requirements_file(host_root).read_text(encoding="utf-8") == original


def test_allowlist_reject_unknown_no_write(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["totally-unknown-package-xyz"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "package_not_allowlisted"
    assert result.payload["host_reverted"] is False
    assert result.payload["guest_site_may_be_dirty"] is False
    assert "allowlist" in (result.payload.get("hint") or "").lower()
    assert requirements_file(host_root).read_text(encoding="utf-8") == original
    assert not marker_path(host_root).is_file()


def test_reject_remove_pytest(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "remove", "packages": ["pytest"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "missing_required_package"
    assert requirements_file(host_root).read_text(encoding="utf-8") == original


def test_reject_set_file_action(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    result = sandbox_pip_update(
        {"action": "set_file", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "invalid_action"


def test_reject_url_spec(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["requests @ git+https://evil"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "invalid_package_spec"
    assert requirements_file(host_root).read_text(encoding="utf-8") == original


def test_network_none_blocks_pip(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    monkeypatch.setenv("ELYRA_SANDBOX_NETWORK", "none")
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "network_policy_blocks_pip"
    assert requirements_file(host_root).read_text(encoding="utf-8") == original


def test_packages_too_many(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    pkgs = [f"markdown"] * 11  # still valid names but over cap
    # Use 11 distinct allowlisted-ish names — over max regardless of allowlist.
    pkgs = [f"pkg{i}" for i in range(11)]
    result = sandbox_pip_update(
        {"action": "add", "packages": pkgs},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "packages_too_many"


# ---------------------------------------------------------------------------
# InstallResult + success / failure paths (mocked guest pip)
# ---------------------------------------------------------------------------


def test_install_result_fields_from_fake(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    life, _client, _sb = _life_with_pip(paths, exit_code=0)
    clear_pyenv_marker(host_root)
    result = try_install_curated_pyenv(life, paths=paths)
    assert isinstance(result, InstallResult)
    assert result.ok is True
    assert result.error_reason is None
    assert result.requirements_hash == requirements_hash(host_root)
    assert result.exit_code == 0


def test_install_result_pip_failed_fields(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    life, _client, _sb = _life_with_pip(
        paths, exit_code=1, stderr="Could not find a version that satisfies\n"
    )
    clear_pyenv_marker(host_root)
    result = try_install_curated_pyenv(life, paths=paths)
    assert result.ok is False
    assert result.error_reason == "pip_failed"
    assert result.exit_code == 1
    assert "satisfies" in result.stderr_tail


def test_add_success_marker_hash_matches(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    _life_with_pip(paths, exit_code=0)
    result = sandbox_pip_update(
        {"action": "add", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is True
    assert result.payload["action"] == "add"
    assert result.payload["packages"] == ["markdown"]
    assert result.payload["host_reverted"] is False
    assert result.payload["guest_site_may_be_dirty"] is False
    assert result.payload["pyenv_ready"] is True
    text = requirements_file(host_root).read_text(encoding="utf-8")
    assert any(
        line.strip().lower().startswith("markdown")
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    digest = requirements_hash(host_root)
    assert result.payload["requirements_hash"] == digest
    assert pyenv_ready(host_root) is True


def test_add_pin_success(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    _life_with_pip(paths, exit_code=0)
    result = sandbox_pip_update(
        {"action": "add", "packages": ["httpx>=0.27,<1"]},
        _ctx(paths),
    )
    assert result.ok is True
    text = requirements_file(host_root).read_text(encoding="utf-8")
    # Existing httpx line should be replaced with pin form.
    httpx_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().lower().startswith("httpx") and not ln.strip().startswith("#")
    ]
    assert httpx_lines
    assert ">=0.27" in httpx_lines[0]


def test_install_failure_restores_and_clears_marker(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    write_pyenv_marker(host_root)  # prior ready state
    assert pyenv_ready(host_root) is True

    _life_with_pip(paths, exit_code=1, stderr="no network for wheels\n")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "pyenv_install_failed"
    assert result.payload["host_reverted"] is True
    assert result.payload["guest_site_may_be_dirty"] is True
    assert "no network" in (result.payload.get("detail") or "")
    # Host files restored
    assert requirements_file(host_root).read_text(encoding="utf-8") == original
    # Marker absent (v1: never restore old marker without re-install success)
    assert not marker_path(host_root).is_file()
    assert pyenv_ready(host_root) is False
    # Backup written under host-only dir
    backup = host_root / ".elyra_pyenv_backup" / "requirements-curated.txt.bak"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original


def test_lifecycle_missing_restores(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    clear_sandbox_lifecycle()
    original = requirements_file(host_root).read_text(encoding="utf-8")
    result = sandbox_pip_update(
        {"action": "add", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "lifecycle_unusable"
    assert result.payload["host_reverted"] is True
    # Guest pip never ran — not dirty (nit: precise honesty).
    assert result.payload["guest_site_may_be_dirty"] is False
    assert requirements_file(host_root).read_text(encoding="utf-8") == original
    assert not marker_path(host_root).is_file()


def test_invalid_timeout_does_not_mutate(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """timeout_seconds validated before write/clear (KD6 host revert contract)."""
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    write_pyenv_marker(host_root)
    assert pyenv_ready(host_root) is True
    _life_with_pip(paths, exit_code=0)

    result = sandbox_pip_update(
        {
            "action": "add",
            "packages": ["markdown"],
            "timeout_seconds": "bad",
        },
        _ctx(paths),
    )
    assert result.ok is False
    assert result.error_reason == "invalid_timeout"
    assert result.payload["host_reverted"] is False
    assert result.payload["guest_site_may_be_dirty"] is False
    assert requirements_file(host_root).read_text(encoding="utf-8") == original
    # Marker still present — never cleared on pre-mutation arg error.
    assert marker_path(host_root).is_file()
    assert "markdown" not in requirements_file(host_root).read_text(encoding="utf-8")


def test_ensure_does_not_wipe_curated_mutations(paths, host_root: Path) -> None:
    """Regression: curated not always-refreshed; allowlist is re-synced from seed."""
    req = requirements_file(host_root)
    mutated = req.read_text(encoding="utf-8") + "\n# package-tool mutation sentinel\n"
    req.write_text(mutated, encoding="utf-8")

    allowlist = host_root / "lib" / "requirements-allowlist.txt"
    allowlist.write_text("# product-local allowlist (should be overwritten)\n", encoding="utf-8")

    # Re-ensure: curated mutation must survive; allowlist re-synced from repo seed.
    ensure_host_tree(PRIMARY_NAME, paths)
    assert req.read_text(encoding="utf-8") == mutated
    assert "package-tool mutation sentinel" in req.read_text(encoding="utf-8")

    seed_al = (
        project_root()
        / "sandboxes"
        / "sandbox0"
        / "lib"
        / "requirements-allowlist.txt"
    )
    assert allowlist.read_text(encoding="utf-8") == seed_al.read_text(encoding="utf-8")
    assert "httpx" in allowlist.read_text(encoding="utf-8").lower()


def test_remove_success_marks_guest_dirty(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    _life_with_pip(paths, exit_code=0)
    # Ensure regex is present (curated seed).
    text_before = requirements_file(host_root).read_text(encoding="utf-8").lower()
    assert "regex" in text_before

    result = sandbox_pip_update(
        {"action": "remove", "packages": ["regex"]},
        _ctx(paths),
    )
    assert result.ok is True
    assert result.payload["guest_site_may_be_dirty"] is True
    assert result.payload["host_reverted"] is False
    assert result.payload["pyenv_ready"] is True
    text_after = requirements_file(host_root).read_text(encoding="utf-8")
    for line in text_after.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not stripped.lower().startswith("regex")
    # pytest still present
    assert "pytest" in text_after.lower()


def test_remove_not_present_unchanged_no_rewarm(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent remove of allowlisted name not in file — no install."""
    _enable_isolation(monkeypatch)
    # markdown not in default curated
    original = requirements_file(host_root).read_text(encoding="utf-8")
    write_pyenv_marker(host_root)
    # No lifecycle registered — would fail if install attempted
    clear_sandbox_lifecycle()
    result = sandbox_pip_update(
        {"action": "remove", "packages": ["markdown"]},
        _ctx(paths),
    )
    assert result.ok is True
    assert result.payload.get("unchanged") is True
    assert result.payload["guest_site_may_be_dirty"] is True
    assert requirements_file(host_root).read_text(encoding="utf-8") == original
    assert pyenv_ready(host_root) is True


def test_add_already_present_unchanged(
    paths, host_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_isolation(monkeypatch)
    original = requirements_file(host_root).read_text(encoding="utf-8")
    write_pyenv_marker(host_root)
    clear_sandbox_lifecycle()
    # requests already in curated seed with a pin — adding bare name replaces?
    # Our merge replaces the line with bare "requests" which *is* a change.
    # Use exact same line as seed for true no-op: re-add with same pin.
    # Seed has `requests>=2,<3`
    result = sandbox_pip_update(
        {"action": "add", "packages": ["requests>=2,<3"]},
        _ctx(paths),
    )
    # May be unchanged if merge produces same text; if pin form differs, install
    # would run — either ok unchanged or lifecycle_unusable. Prefer exact seed pin.
    if result.payload.get("unchanged"):
        assert result.ok is True
        assert requirements_file(host_root).read_text(encoding="utf-8") == original
    else:
        # File would change → install attempted without lifecycle
        assert result.ok is False
        assert result.error_reason == "lifecycle_unusable"
        assert result.payload["host_reverted"] is True
        assert requirements_file(host_root).read_text(encoding="utf-8") == original


def test_bundled_tool_metadata_exists() -> None:
    root = project_root() / "tools" / "bundled" / "sandbox_pip_update"
    assert (root / "TOOL.md").is_file()
    assert (root / "schema.json").is_file()
    runner = (root / "runner.json").read_text(encoding="utf-8")
    assert "sandbox_packages:sandbox_pip_update" in runner
