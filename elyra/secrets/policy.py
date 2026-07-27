"""Secrets name rules, grants, and inject maps (host builtins v1).

Scope: reserved names, secret name validation, TOOL_SECRET_REQUIREMENTS,
secret→env mapping, SECRET_WRITE_TOOLS / arg keys for chain scrub.
Out of scope: store I/O, Glass API, RunnerSpec extension.
"""

from __future__ import annotations

import re
from typing import Any

# Filenames / dir names co-resident under data/secrets/ that must not be
# treated as named operator secrets (llm.auth + store layout).
RESERVED_SECRET_NAMES = frozenset(
    {
        "xai_api_key",
        "xai_api_key.tmp",
        "meta.json",
        "values",
    }
)

# Single path segment under values/: letter/digit start; alnum, underscore, hyphen, dot.
SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# v1 host builtins hardcode required secret names (do not extend RunnerSpec).
TOOL_SECRET_REQUIREMENTS: dict[str, list[str]] = {
    "gh_auth_status": ["gh_token"],
    "gh_pr_create": ["gh_token"],
    "gh_pr_list": ["gh_token"],
    "gh_pr_view": ["gh_token"],
    "gh_issue_create": ["gh_token"],
    "gh_issue_list": ["gh_token"],
    "gh_api": ["gh_token"],
    "gh_project_list": ["gh_token"],
    "gh_project_item_list": ["gh_token"],
    "gh_project_item_add": ["gh_token"],
    "gh_project_item_edit": ["gh_token"],
    "gh_project_field_list": ["gh_token"],
}

# secret name → env var for subprocess inject (host builtins only).
SECRET_ENV_MAP: dict[str, str] = {
    "gh_token": "GH_TOKEN",
}

# Chain scrub: tools whose function.arguments must never prefer arguments_raw.
SECRET_WRITE_TOOLS = frozenset({"secrets_set"})

# Argument keys replaced with "***" for SECRET_WRITE_TOOLS chain serialization.
SECRET_WRITE_ARG_KEYS = frozenset({"value", "secret", "token", "password", "api_key"})

# Redaction placeholder (results + chain args).
REDACT_PLACEHOLDER = "***"

# Managed-by values written to meta.json.
MANAGED_BY_USER = "user"
MANAGED_BY_SYSTEM = "system"


def is_reserved_secret_name(name: str) -> bool:
    """True if ``name`` collides with llm.auth or store layout filenames."""
    if not isinstance(name, str):
        return True
    return name.strip() in RESERVED_SECRET_NAMES or name.strip().casefold() in {
        n.casefold() for n in RESERVED_SECRET_NAMES
    }


def validate_secret_name(name: object) -> str:
    """Return stripped name if valid for values/<name>; raise ValueError otherwise.

    Rejects reserved names, empty, path separators, and unsafe segments.
    """
    if not isinstance(name, str):
        raise ValueError("invalid_secret_name")
    raw = name.strip()
    if not raw:
        raise ValueError("invalid_secret_name")
    if is_reserved_secret_name(raw):
        raise ValueError("reserved_secret_name")
    if not SECRET_NAME_RE.fullmatch(raw):
        raise ValueError("invalid_secret_name")
    if raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise ValueError("invalid_secret_name")
    return raw


def env_var_for_secret(secret_name: str) -> str:
    """Map secret store name to subprocess env var (default UPPER_SNAKE)."""
    mapped = SECRET_ENV_MAP.get(secret_name)
    if mapped:
        return mapped
    return secret_name.upper()


def normalize_grants(grants: Any) -> list[str]:
    """Return a de-duplicated list of non-empty grant tool names (order preserved)."""
    if grants is None:
        return []
    if not isinstance(grants, (list, tuple)):
        raise ValueError("invalid_grants")
    out: list[str] = []
    seen: set[str] = set()
    for item in grants:
        if not isinstance(item, str):
            raise ValueError("invalid_grants")
        g = item.strip()
        if not g:
            continue
        key = g.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


__all__ = [
    "MANAGED_BY_SYSTEM",
    "MANAGED_BY_USER",
    "REDACT_PLACEHOLDER",
    "RESERVED_SECRET_NAMES",
    "SECRET_ENV_MAP",
    "SECRET_NAME_RE",
    "SECRET_WRITE_ARG_KEYS",
    "SECRET_WRITE_TOOLS",
    "TOOL_SECRET_REQUIREMENTS",
    "env_var_for_secret",
    "is_reserved_secret_name",
    "normalize_grants",
    "validate_secret_name",
]
