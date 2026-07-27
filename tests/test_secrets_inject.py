"""PR5: secrets inject hook, guest non-merge, result + chain redaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import ChatCompletionResult, ToolCall as LlmToolCall
from elyra.loop.doloop import assistant_message_from_result
from elyra.secrets.inject import (
    redact_tool_call_arguments,
    redact_tool_result_payload,
    resolve_for_tool,
)
from elyra.secrets.store import SecretsStore
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext, ToolResult


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def test_resolve_for_tool_requires_grant(paths) -> None:
    store = SecretsStore(paths.data_dir)
    store.set_secret("gh_token", "ghs_secret_abc", grants=[])
    env = resolve_for_tool("gh_pr_create", store)
    assert env == {}

    store.set_grants("gh_token", ["gh_pr_create"])
    env = resolve_for_tool("gh_pr_create", store)
    assert env == {"GH_TOKEN": "ghs_secret_abc"}

    # Wrong tool name → no inject
    assert resolve_for_tool("gh_api", store) == {}


def test_registry_sets_secret_env_does_not_invent_auth_unavailable(paths) -> None:
    """Missing secret still dispatches; registry never returns auth_unavailable."""
    reg = ToolRegistry(paths)

    def _handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        secret_env = ctx.extras.get("secret_env") if isinstance(ctx.extras, dict) else None
        return ToolResult(
            ok=True,
            payload={
                "secret_env_keys": sorted((secret_env or {}).keys()),
                "secret_env_empty": not bool(secret_env),
            },
        )

    # Use real secrets_list package (no TOOL_SECRET_REQUIREMENTS) and a synthetic
    # execute path via monkeypatched package lookup for gh_pr_create.
    from elyra.tools.registry import ToolPackage
    from elyra.tools.runner import RunnerSpec
    from elyra.tools.schema import ToolMeta

    pkg = ToolPackage(
        meta=ToolMeta(
            name="gh_pr_create",
            description="test",
            kind="mutate",
            package_dir=paths.tools_dir,
            parameters={"type": "object", "properties": {}},
        ),
        runner=RunnerSpec(kind="builtin", entry="test:handler"),
        source="bundled",
        package_dir=paths.tools_dir,
        handler=_handler,
    )
    reg._by_key["gh_pr_create"] = pkg  # noqa: SLF001 — unit inject

    ctx = ToolContext(paths=paths, extras={})
    result = reg.execute("gh_pr_create", {}, ctx)
    assert result.ok is True
    assert result.error_reason is None
    assert result.error_reason != "auth_unavailable"
    assert result.payload.get("secret_env_empty") is True
    assert result.payload.get("secret_env_keys") == []
    assert ctx.extras.get("secret_env") == {}


def test_registry_injects_when_granted(paths) -> None:
    store = SecretsStore(paths.data_dir)
    store.set_secret(
        "gh_token",
        "ghs_injected_value",
        grants=["gh_pr_create"],
    )
    reg = ToolRegistry(paths)

    captured: dict[str, Any] = {}

    def _handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        captured["secret_env"] = dict(ctx.extras.get("secret_env") or {})
        return ToolResult(ok=True, payload={"ran": True})

    from elyra.tools.registry import ToolPackage
    from elyra.tools.runner import RunnerSpec
    from elyra.tools.schema import ToolMeta

    pkg = ToolPackage(
        meta=ToolMeta(
            name="gh_pr_create",
            description="test",
            kind="mutate",
            package_dir=paths.tools_dir,
            parameters={"type": "object", "properties": {}},
        ),
        runner=RunnerSpec(kind="builtin", entry="test:handler"),
        source="bundled",
        package_dir=paths.tools_dir,
        handler=_handler,
    )
    reg._by_key["gh_pr_create"] = pkg  # noqa: SLF001
    ctx = ToolContext(paths=paths, extras={})
    result = reg.execute("gh_pr_create", {}, ctx)
    assert result.ok
    assert captured["secret_env"] == {"GH_TOKEN": "ghs_injected_value"}


def test_registry_redacts_payload_secret_values(paths) -> None:
    secret = "ghs_must_not_leak_in_payload"
    store = SecretsStore(paths.data_dir)
    store.set_secret("gh_token", secret, grants=["gh_pr_create"])
    reg = ToolRegistry(paths)

    def _handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            payload={"stdout": f"token={secret}", "nested": {"t": secret}},
        )

    from elyra.tools.registry import ToolPackage
    from elyra.tools.runner import RunnerSpec
    from elyra.tools.schema import ToolMeta

    pkg = ToolPackage(
        meta=ToolMeta(
            name="gh_pr_create",
            description="test",
            kind="mutate",
            package_dir=paths.tools_dir,
            parameters={"type": "object", "properties": {}},
        ),
        runner=RunnerSpec(kind="builtin", entry="test:handler"),
        source="bundled",
        package_dir=paths.tools_dir,
        handler=_handler,
    )
    reg._by_key["gh_pr_create"] = pkg  # noqa: SLF001
    result = reg.execute("gh_pr_create", {}, ToolContext(paths=paths, extras={}))
    blob = json.dumps(result.payload)
    assert secret not in blob
    assert "***" in result.payload["stdout"]


def test_secrets_set_tool_result_omits_value(paths) -> None:
    reg = ToolRegistry(paths)
    assert reg.has("secrets_set")
    secret = "never-in-result-please"
    ctx = ToolContext(paths=paths, extras={})
    result = reg.execute(
        "secrets_set",
        {"name": "gh_token", "value": secret, "grants": ["gh_api"]},
        ctx,
    )
    assert result.ok is True
    blob = json.dumps(result.payload)
    assert secret not in blob
    assert "value" not in result.payload or result.payload.get("value") != secret
    assert result.payload.get("name") == "gh_token"


def test_chain_redacts_secrets_set_arguments_raw() -> None:
    """K6: chain must not use unredacted arguments_raw for secrets_set."""
    secret = "RAW_SECRET_VALUE_IN_ARGUMENTS_RAW_xyz"
    raw = json.dumps(
        {"name": "gh_token", "value": secret, "grants": ["gh_api"]},
        ensure_ascii=False,
    )
    tc = LlmToolCall(
        id="call_1",
        name="secrets_set",
        arguments={"name": "gh_token", "value": secret, "grants": ["gh_api"]},
        arguments_raw=raw,
        arguments_parse_ok=True,
    )
    result = ChatCompletionResult(
        content="",
        reasoning_content="",
        raw_json="{}",
        tool_calls=[tc],
    )
    msg = assistant_message_from_result(result)
    args_s = msg["tool_calls"][0]["function"]["arguments"]
    assert secret not in args_s
    assert "***" in args_s
    parsed = json.loads(args_s)
    assert parsed["name"] == "gh_token"
    assert parsed["value"] == "***"


def test_chain_redacts_when_only_arguments_raw_has_secret() -> None:
    """Parse failure path: load from arguments_raw then redact."""
    secret = "ONLY_IN_RAW_secret_99"
    raw = json.dumps({"name": "gh_token", "value": secret}, ensure_ascii=False)
    tc = LlmToolCall(
        id="call_2",
        name="secrets_set",
        arguments={},  # parse failed → empty
        arguments_raw=raw,
        arguments_parse_ok=False,
    )
    msg = assistant_message_from_result(
        ChatCompletionResult(
            content="",
            reasoning_content="",
            raw_json="{}",
            tool_calls=[tc],
        )
    )
    args_s = msg["tool_calls"][0]["function"]["arguments"]
    assert secret not in args_s
    assert "***" in args_s


def test_chain_other_tools_still_prefer_arguments_raw() -> None:
    raw = '{"path":"/tmp/x"}'
    tc = LlmToolCall(
        id="c",
        name="read_file",
        arguments={"path": "/other"},
        arguments_raw=raw,
        arguments_parse_ok=True,
    )
    msg = assistant_message_from_result(
        ChatCompletionResult(
            content="",
            reasoning_content="",
            raw_json="{}",
            tool_calls=[tc],
        )
    )
    assert msg["tool_calls"][0]["function"]["arguments"] == raw


def test_guest_exec_scrubbed_env_never_merges_secret_env(paths) -> None:
    """guest_exec / host-stub must ignore secret_env (contract)."""
    from elyra.tools.guest_exec import _scrubbed_host_env

    secret = "must-not-appear-in-guest-env"
    # Simulate what a buggy merge would look like — assert scrub helper
    # does not pull process env secrets either, and our registry path
    # never passes secret_env into extra=.
    env = _scrubbed_host_env(home=paths.home, extra={"FOO": "bar"})
    assert "GH_TOKEN" not in env
    assert secret not in env.values()
    assert env.get("FOO") == "bar"

    # Monkeypatch: if dispatch for sandbox_shell is invoked after inject,
    # the scrubbed env must not contain secret values even if secret_env is set.
    store = SecretsStore(paths.data_dir)
    store.set_secret("gh_token", secret, grants=["gh_pr_create"])
    reg = ToolRegistry(paths)

    seen_envs: list[dict[str, str]] = []

    real_scrub = _scrubbed_host_env

    def _tracking_scrub(*, home, extra=None):
        e = real_scrub(home=home, extra=extra)
        seen_envs.append(dict(e))
        # Assert no secret value leaked into scrubbed env.
        assert secret not in e.values()
        assert e.get("GH_TOKEN") != secret
        return e

    with patch("elyra.tools.guest_exec._scrubbed_host_env", side_effect=_tracking_scrub):
        # Build a minimal sandbox_shell package and execute — may fail without
        # binary, but env scrub is what we assert.
        from elyra.tools.registry import ToolPackage
        from elyra.tools.runner import RunnerSpec
        from elyra.tools.schema import ToolMeta

        pkg_dir = paths.tools_dir / "local" / "echo_env_shell"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "schema.json").write_text("{}", encoding="utf-8")
        (pkg_dir / "runner.json").write_text(
            json.dumps({"kind": "sandbox_shell", "argv": ["true"]}),
            encoding="utf-8",
        )
        (pkg_dir / "TOOL.md").write_text(
            "---\nname: echo_env_shell\ndescription: t\nkind: read\n---\n",
            encoding="utf-8",
        )
        # Not in TOOL_SECRET_REQUIREMENTS — still attach empty secret_env via
        # registry path; for shell, ensure scrub never sees secrets.
        reg.reload()
        if reg.has("echo_env_shell"):
            ctx = ToolContext(paths=paths, extras={"secret_env": {"GH_TOKEN": secret}})
            reg.execute("echo_env_shell", {}, ctx)
            # If host-stub ran, scrub was called without secret merge.
            for e in seen_envs:
                assert secret not in e.values()


def test_redact_tool_call_arguments_keys() -> None:
    out = redact_tool_call_arguments(
        "secrets_set",
        {"name": "x", "value": "s", "token": "t", "other": "keep"},
    )
    assert out["value"] == "***"
    assert out["token"] == "***"
    assert out["other"] == "keep"
    assert out["name"] == "x"


def test_redact_payload_recursive() -> None:
    secret = "leakme"
    payload = {"a": secret, "b": [secret, {"c": secret}]}
    out = redact_tool_result_payload(payload, [secret])
    assert out == {"a": "***", "b": ["***", {"c": "***"}]}
