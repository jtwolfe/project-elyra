"""Grok Build instrument package — broker modules for host ``grok_build``.

Scope: mode/argv/validate/result/redact + process/auth (PR2) + jobs/reaper/
usage_bridge (PR3). Public exports stay narrow; long-mode readiness still
depends on supervisor wire + later builtin registration (PR4).
"""

from __future__ import annotations

from elyra.instrument.argv import (
    HUMAN_GATE_POLICY,
    build_argv_for_mode,
    build_cli_argv,
    build_slash_prompt,
)
from elyra.instrument.jobs import (
    ensure_grok_build_runtime,
    create_job,
    load_job,
    update_job_status,
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
from elyra.instrument.usage_bridge import (
    adapt_headless_usage,
    messages_usage_to_token_usage,
    meter_allows_call,
    record_instrument_usage,
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
    "adapt_headless_usage",
    "build_argv_for_mode",
    "build_cli_argv",
    "build_slash_prompt",
    "create_job",
    "default_timeout_s",
    "defaults_async",
    "ensure_grok_build_runtime",
    "harvest_artifacts",
    "is_poll_only",
    "load_job",
    "make_error_payload",
    "make_success_payload",
    "merge_known_values",
    "messages_usage_to_token_usage",
    "meter_allows_call",
    "parse_artifact_paths_from_text",
    "parse_needs_human",
    "record_instrument_usage",
    "redact_instrument_result",
    "redact_result_payload",
    "tool_result_dict",
    "update_job_status",
    "validate_grok_build_args",
]
