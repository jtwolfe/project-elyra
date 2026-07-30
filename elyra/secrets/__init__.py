"""Named secrets store, inject hook, and redaction (PR5).

File backend under ``data/secrets/`` coexists with ``elyra.llm.auth``
(``xai_api_key`` reserved). Host builtins read ``ctx.extras["secret_env"]``;
guest / host-stub paths must never merge secret_env.
"""

from elyra.secrets.inject import (
    GROK_BUILD_TOOL_NAMES,
    redact_payload,
    redact_tool_call_arguments,
    redact_tool_result_payload,
    resolve_access_token_for_tool,
    resolve_for_tool,
)
from elyra.secrets.policy import (
    REDACT_PLACEHOLDER,
    RESERVED_SECRET_NAMES,
    SECRET_WRITE_ARG_KEYS,
    SECRET_WRITE_TOOLS,
    TOOL_SECRET_REQUIREMENTS,
    validate_secret_name,
)
from elyra.secrets.store import SecretsStore

__all__ = [
    "GROK_BUILD_TOOL_NAMES",
    "REDACT_PLACEHOLDER",
    "RESERVED_SECRET_NAMES",
    "SECRET_WRITE_ARG_KEYS",
    "SECRET_WRITE_TOOLS",
    "SecretsStore",
    "TOOL_SECRET_REQUIREMENTS",
    "redact_payload",
    "redact_tool_call_arguments",
    "redact_tool_result_payload",
    "resolve_access_token_for_tool",
    "resolve_for_tool",
    "validate_secret_name",
]
