"""Grok Build instrument package — pure core (PR1).

Scope: mode/argv/validate/result/redact public surface for the host
``grok_build`` tool. No subprocess, network, or OAuth refresh in this package
slice (process/auth/jobs land in later PRs).

Public exports are intentionally narrow: Mode + defaults, validation,
slash/argv builders, harvest helpers, redaction.
"""

from __future__ import annotations

from elyra.instrument.argv import (
    HUMAN_GATE_POLICY,
    build_argv_for_mode,
    build_cli_argv,
    build_slash_prompt,
)
from elyra.instrument.modes import (
    ASYNC_TIMEOUT_THRESHOLD_S,
    DEEP_RESEARCH_EXPERIMENTAL,
    DEFAULT_BASE_BRANCH,
    DEFAULT_TIMEOUT_S,
    Mode,
    default_timeout_s,
    defaults_async,
)
from elyra.instrument.redact import (
    merge_known_values,
    redact_instrument_result,
    redact_result_payload,
)
from elyra.instrument.result import (
    harvest_artifacts,
    make_error_payload,
    make_success_payload,
    parse_artifact_paths_from_text,
    parse_needs_human,
    tool_result_dict,
)
from elyra.instrument.validate import (
    is_poll_only,
    validate_grok_build_args,
)

__all__ = [
    "ASYNC_TIMEOUT_THRESHOLD_S",
    "DEEP_RESEARCH_EXPERIMENTAL",
    "DEFAULT_BASE_BRANCH",
    "DEFAULT_TIMEOUT_S",
    "HUMAN_GATE_POLICY",
    "Mode",
    "build_argv_for_mode",
    "build_cli_argv",
    "build_slash_prompt",
    "default_timeout_s",
    "defaults_async",
    "harvest_artifacts",
    "is_poll_only",
    "make_error_payload",
    "make_success_payload",
    "merge_known_values",
    "parse_artifact_paths_from_text",
    "parse_needs_human",
    "redact_instrument_result",
    "redact_result_payload",
    "tool_result_dict",
    "validate_grok_build_args",
]
