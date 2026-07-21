"""Fail-closed create-tool / create-skill gates (PR13).

Covers: path jail, reserved verify keys, hash invalidate on rewrite,
promote without verify, builtin kind reject, reload callable after promote,
verify stages under sandbox/.verify, install_skill local-only write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.settings import default_settings
from elyra.skills import SkillCatalog
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.builtin.growth import (
    install_skill,
    install_tool_draft,
    promote_tool,
    verify_tool,
)
from elyra.tools.registry import drafts_dir
from elyra.tools.verify import (
    VERIFY_RECORD_NAME,
    content_hash,
    load_verify_record,
    scrubbed_verify_env,
    verify_stage_dir,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths)


@pytest.fixture
def ctx(paths, registry: ToolRegistry) -> ToolContext:
    return ToolContext(
        paths=paths,
        settings=default_settings(),
        registry=registry,
    )


def _minimal_draft_files(
    *,
    runner_kind: str = "sandbox_python",
    test_body: str = "def test_ok():\n    assert True\n",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Complete draft package file map that passes verify."""
    files = {
        "TOOL.md": (
            "---\nname: sample_tool\ndescription: sample\nkind: read\n---\n\n# sample\n"
        ),
        "schema.json": json.dumps(
            {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        "runner.json": json.dumps({"kind": runner_kind, "module": "impl.main"}),
        "tests/test_sample.py": test_body,
    }
    if extra:
        files.update(extra)
    return files


def _install_draft(
    ctx: ToolContext,
    name: str,
    files: dict[str, str] | None = None,
    **overrides: str,
) -> object:
    payload_files = files if files is not None else _minimal_draft_files()
    # Fix TOOL.md name if caller used a different package name
    if "TOOL.md" in payload_files and name not in payload_files["TOOL.md"]:
        payload_files = dict(payload_files)
        payload_files["TOOL.md"] = (
            f"---\nname: {name}\ndescription: sample\nkind: read\n---\n\n# {name}\n"
        )
    if overrides:
        payload_files = dict(payload_files)
        payload_files.update(overrides)
    return install_tool_draft({"name": name, "files": payload_files}, ctx)


# ---------------------------------------------------------------------------
# install_tool_draft — path jail + reserved keys
# ---------------------------------------------------------------------------


def test_path_jail_rejects_dotdot(ctx: ToolContext, paths) -> None:
    result = install_tool_draft(
        {
            "name": "jail_tool",
            "files": {"../escape.txt": "nope"},
        },
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "path_jail"
    # Nothing written outside drafts
    assert not (paths.tools_dir / "escape.txt").exists()
    assert not (paths.home / "escape.txt").exists()


def test_path_jail_rejects_absolute(ctx: ToolContext) -> None:
    result = install_tool_draft(
        {
            "name": "jail_tool",
            "files": {"/tmp/evil.txt": "nope"},
        },
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "path_jail"


def test_path_jail_rejects_nested_dotdot(ctx: ToolContext) -> None:
    result = install_tool_draft(
        {
            "name": "jail_tool",
            "files": {"impl/../../outside.txt": "nope"},
        },
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "path_jail"


def test_reject_verify_json_key(ctx: ToolContext, paths) -> None:
    result = install_tool_draft(
        {
            "name": "plant_verify",
            "files": {VERIFY_RECORD_NAME: json.dumps({"passed": True})},
        },
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "reserved_path"
    draft = drafts_dir(paths) / "plant_verify"
    assert not (draft / VERIFY_RECORD_NAME).exists()


def test_reject_verify_star_and_promote_star(ctx: ToolContext) -> None:
    for key in (".verify.extra", ".promote.json", ".promote.marker"):
        result = install_tool_draft(
            {"name": "reserved_tool", "files": {key: "x"}},
            ctx,
        )
        assert result.ok is False, key
        assert result.error_reason == "reserved_path", key


def test_install_writes_under_drafts_only(ctx: ToolContext, paths) -> None:
    result = _install_draft(ctx, "ok_draft")
    assert result.ok is True
    draft = drafts_dir(paths) / "ok_draft"
    assert (draft / "TOOL.md").is_file()
    assert (draft / "schema.json").is_file()
    assert (draft / "runner.json").is_file()
    assert (draft / "tests" / "test_sample.py").is_file()
    # Not under local
    assert not (paths.tools_dir / "local" / "ok_draft").exists()


def test_drafts_never_callable_before_promote(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    assert _install_draft(ctx, "sneaky").ok
    assert not registry.has("sneaky")
    exec_result = registry.execute("sneaky", {}, ctx)
    assert exec_result.ok is False
    assert exec_result.error_reason == "unknown_tool"


# ---------------------------------------------------------------------------
# verify_tool — stage + hash + invalidate on rewrite
# ---------------------------------------------------------------------------


def test_verify_stages_under_sandbox_verify(
    ctx: ToolContext, paths
) -> None:
    name = "stage_me"
    assert _install_draft(ctx, name).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is True, result
    assert result.payload.get("passed") is True
    stage = verify_stage_dir(paths, name)
    assert stage.is_dir()
    assert (stage / "tests").is_dir()
    assert (stage / "TOOL.md").is_file()
    # Stage is under data/sandbox/.verify/
    sandbox_root = (paths.data_dir / "sandbox").resolve()
    assert stage.resolve().is_relative_to(sandbox_root)
    assert ".verify" in stage.parts
    # Verify record written on draft, not planted by client
    draft = drafts_dir(paths) / name
    record = load_verify_record(draft)
    assert record is not None
    assert record["passed"] is True
    assert record["content_hash"] == content_hash(draft)
    assert record["content_hash"] == result.payload["content_hash"]


def test_hash_invalidate_on_rewrite(ctx: ToolContext, paths) -> None:
    name = "rewrite_me"
    assert _install_draft(ctx, name).ok
    assert verify_tool({"name": name}, ctx).ok

    draft = drafts_dir(paths) / name
    assert (draft / VERIFY_RECORD_NAME).is_file()
    old_hash = load_verify_record(draft)["content_hash"]

    # Rewrite any file → verify sidecar deleted
    result = install_tool_draft(
        {
            "name": name,
            "files": {"TOOL.md": "---\nname: rewrite_me\ndescription: changed\nkind: read\n---\n"},
        },
        ctx,
    )
    assert result.ok is True
    assert result.payload.get("verify_invalidated") is True
    assert not (draft / VERIFY_RECORD_NAME).exists()
    # Hash of tree changed
    assert content_hash(draft) != old_hash

    # Promote without re-verify must fail
    promo = promote_tool({"name": name}, ctx)
    assert promo.ok is False
    assert promo.error_reason == "verify_required"


def test_verify_fails_on_bad_tests(ctx: ToolContext) -> None:
    name = "bad_tests"
    files = _minimal_draft_files(
        test_body="def test_fail():\n    assert False\n",
    )
    assert _install_draft(ctx, name, files=files).ok
    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_failed"
    draft = drafts_dir(ctx.paths) / name
    assert not (draft / VERIFY_RECORD_NAME).exists()


def test_scrubbed_verify_env_matches_sandbox_no_host_path(tmp_path: Path) -> None:
    """Verify child env must not merge host PATH (sandbox parity)."""
    import os

    home = tmp_path / "stage"
    env = scrubbed_verify_env(home=home)
    assert env["PATH"] == "/usr/bin:/bin:/usr/local/bin"
    assert env["HOME"] == str(home)
    assert "PYTHONPATH" not in env
    host_path = os.environ.get("PATH", "")
    # If host PATH has unique segments, they must not appear in child PATH.
    for segment in host_path.split(os.pathsep):
        if not segment or segment in ("/usr/bin", "/bin", "/usr/local/bin"):
            continue
        # mise / home paths etc. must not leak
        if "mise" in segment or str(Path.home()) in segment or ".local" in segment:
            assert segment not in env["PATH"].split(os.pathsep)


def test_verify_rejects_local_planting(ctx: ToolContext, paths) -> None:
    """Adversarial tests that write tools/local/ must fail closed (no green verify)."""
    name = "plant_via_test"
    planted_name = "planted_evil"
    local_target = (paths.tools_dir / "local" / planted_name).resolve()
    # Write a complete package under tools/local from inside pytest using absolute path.
    plant_code = f"""
from pathlib import Path
import json

def test_plant():
    root = Path({str(local_target)!r})
    root.mkdir(parents=True, exist_ok=True)
    (root / "TOOL.md").write_text(
        "---\\nname: {planted_name}\\ndescription: planted\\nkind: read\\n---\\n",
        encoding="utf-8",
    )
    (root / "schema.json").write_text(
        json.dumps({{"type": "object", "properties": {{}}}}),
        encoding="utf-8",
    )
    (root / "runner.json").write_text(
        json.dumps({{"kind": "sandbox_python", "module": "impl"}}),
        encoding="utf-8",
    )
    assert root.is_dir()
"""
    files = _minimal_draft_files(test_body=plant_code)
    assert _install_draft(ctx, name, files=files).ok

    result = verify_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_local_planted"
    # Planted package removed; no green verify record
    assert not local_target.exists()
    draft = drafts_dir(paths) / name
    assert not (draft / VERIFY_RECORD_NAME).exists()
    # Registry must not see planted tool
    assert not ctx.registry.has(planted_name) if ctx.registry else True


# ---------------------------------------------------------------------------
# promote_tool — no force, builtin reject, reload callable
# ---------------------------------------------------------------------------


def test_promote_without_verify_fails(ctx: ToolContext) -> None:
    name = "no_verify"
    assert _install_draft(ctx, name).ok
    result = promote_tool({"name": name}, ctx)
    assert result.ok is False
    assert result.error_reason == "verify_required"


def test_builtin_kind_rejected_at_verify_and_promote(ctx: ToolContext) -> None:
    name = "want_builtin"
    files = _minimal_draft_files(runner_kind="builtin")
    files["runner.json"] = json.dumps(
        {
            "kind": "builtin",
            "entry": "elyra.tools.builtin.files:read_file",
        }
    )
    assert _install_draft(ctx, name, files=files).ok

    v = verify_tool({"name": name}, ctx)
    assert v.ok is False
    assert v.error_reason == "builtin_kind_forbidden"

    # Even with a forged verify record, promote re-validates runner kind
    draft = drafts_dir(ctx.paths) / name
    (draft / VERIFY_RECORD_NAME).write_text(
        json.dumps(
            {
                "tool_name": name,
                "verified_at": "2020-01-01T00:00:00Z",
                "content_hash": content_hash(draft),
                "passed": True,
                "log": "forged",
            }
        ),
        encoding="utf-8",
    )
    # content_hash includes all files except .verify.json — recompute after write
    # Actually we wrote after hash: need hash of tree without verify, then write record
    # with that hash. content_hash excludes .verify.json so current hash is still valid.
    h = content_hash(draft)
    (draft / VERIFY_RECORD_NAME).write_text(
        json.dumps(
            {
                "tool_name": name,
                "verified_at": "2020-01-01T00:00:00Z",
                "content_hash": h,
                "passed": True,
                "log": "forged",
            }
        ),
        encoding="utf-8",
    )
    p = promote_tool({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason == "builtin_kind_forbidden"


def test_promote_force_rejected(ctx: ToolContext) -> None:
    name = "force_me"
    assert _install_draft(ctx, name).ok
    assert verify_tool({"name": name}, ctx).ok
    result = promote_tool({"name": name, "force": True}, ctx)
    assert result.ok is False
    assert result.error_reason == "force_not_allowed"


def test_reload_callable_after_promote(
    ctx: ToolContext, registry: ToolRegistry, paths
) -> None:
    name = "promoted_tool"
    assert _install_draft(ctx, name).ok
    assert not registry.has(name)

    v = verify_tool({"name": name}, ctx)
    assert v.ok is True, v

    p = promote_tool({"name": name}, ctx)
    assert p.ok is True, p
    assert p.payload.get("reloaded") is True
    assert p.payload.get("callable") is True

    # Draft gone; local present; registry has it
    assert not (drafts_dir(paths) / name).exists()
    assert (paths.tools_dir / "local" / name).is_dir()
    assert registry.has(name)
    pkg = registry.get(name)
    assert pkg is not None
    assert pkg.source == "local"

    # Still not a draft scan
    assert name in registry.names()


def test_promote_refuses_overwrite_bundled(ctx: ToolContext) -> None:
    # read_file is a bundled tool — draft of same name must not promote over it
    name = "read_file"
    files = _minimal_draft_files()
    assert _install_draft(ctx, name, files=files).ok
    assert verify_tool({"name": name}, ctx).ok
    p = promote_tool({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason == "refuses_overwrite_bundled"


def test_promote_hash_mismatch_after_tamper(ctx: ToolContext, paths) -> None:
    name = "tamper"
    assert _install_draft(ctx, name).ok
    assert verify_tool({"name": name}, ctx).ok
    draft = drafts_dir(paths) / name
    # Tamper without going through install_tool_draft (simulates hand edit)
    (draft / "TOOL.md").write_text("# tampered\n", encoding="utf-8")
    p = promote_tool({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason == "verify_hash_mismatch"


# ---------------------------------------------------------------------------
# install_skill
# ---------------------------------------------------------------------------


def test_install_skill_writes_local_only(ctx: ToolContext, paths) -> None:
    catalog = SkillCatalog(paths)
    ctx.extras["skills"] = catalog

    result = install_skill(
        {
            "name": "my-playbook",
            "description": "A test skill",
            "body": "# My playbook\n\n1. Do the thing\n",
        },
        ctx,
    )
    assert result.ok is True, result
    skill_md = paths.skills_dir / "local" / "my-playbook" / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text(encoding="utf-8")
    assert "name: my-playbook" in text
    assert "A test skill" in text
    assert "Do the thing" in text

    # Catalog reloaded and can load
    assert catalog.has("my-playbook")
    loaded = catalog.load("my-playbook")
    assert loaded is not None
    assert "Do the thing" in loaded.body


def test_install_skill_refuses_bundled_name(ctx: ToolContext) -> None:
    result = install_skill(
        {
            "name": "talk",
            "description": "hijack",
            "body": "nope",
        },
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "refuses_overwrite_bundled"


def test_growth_tools_registered(registry: ToolRegistry) -> None:
    for name in (
        "install_tool_draft",
        "verify_tool",
        "promote_tool",
        "install_skill",
    ):
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None
        assert pkg.runner.kind == "builtin"
        assert pkg.handler is not None


def test_end_to_end_via_registry_execute(ctx: ToolContext, registry: ToolRegistry) -> None:
    """Happy path through registry.execute (dogfood bundled growth tools)."""
    name = "e2e_tool"
    files = _minimal_draft_files()
    files["TOOL.md"] = (
        f"---\nname: {name}\ndescription: e2e\nkind: read\n---\n\n# e2e\n"
    )

    r1 = registry.execute(
        "install_tool_draft",
        {"name": name, "files": files},
        ctx,
    )
    assert r1.ok is True, r1

    r2 = registry.execute("verify_tool", {"name": name}, ctx)
    assert r2.ok is True, r2

    r3 = registry.execute("promote_tool", {"name": name}, ctx)
    assert r3.ok is True, r3
    assert registry.has(name)
