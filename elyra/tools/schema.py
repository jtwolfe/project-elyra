"""Load tool package metadata and JSON Schema; emit OpenAI tools shapes.

Scope: TOOL.md frontmatter, schema.json, OpenAI function tool dicts.
In scope: parse name/description/kind, parameters object validation.
Out of scope: runner dispatch, arg runtime validation against schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# YAML-ish frontmatter between --- fences (stdlib only; no PyYAML dep).
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


@dataclass(frozen=True)
class ToolMeta:
    """Parsed package identity from TOOL.md + directory name."""

    name: str
    description: str
    kind: str  # read | mutate | speak | control | integrate | ""
    package_dir: Path
    parameters: dict[str, Any]  # JSON Schema object for arguments


def load_schema_json(package_dir: Path) -> dict[str, Any]:
    """Load and minimally validate ``schema.json`` (JSON Schema parameters)."""
    path = package_dir / "schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing schema.json in {package_dir}")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"schema.json must be a JSON object: {path}")
    # JSON Schema for tool args is an object schema (type: object).
    schema_type = data.get("type")
    if schema_type is not None and schema_type != "object":
        raise ValueError(
            f"schema.json type must be 'object' (or omitted), got {schema_type!r}: {path}"
        )
    return data


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple key: value frontmatter (no nested YAML)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            fields[key] = value
    return fields


def load_tool_meta(package_dir: Path, *, default_name: str | None = None) -> ToolMeta:
    """Load TOOL.md + schema.json for a package directory.

    Directory basename is the authority for the callable name when frontmatter
    omits ``name`` (dogfood: folder name = tool name).
    """
    package_dir = Path(package_dir)
    dir_name = package_dir.name
    name = default_name or dir_name
    description = ""
    kind = ""

    tool_md = package_dir / "TOOL.md"
    if tool_md.is_file():
        text = tool_md.read_text(encoding="utf-8")
        fields = _parse_frontmatter(text)
        if fields.get("name"):
            name = fields["name"].strip()
        if fields.get("description"):
            description = fields["description"].strip()
        if fields.get("kind"):
            kind = fields["kind"].strip().lower()
        # Body first non-empty line as fallback description
        if not description:
            body = _FRONTMATTER_RE.sub("", text, count=1).strip()
            for line in body.splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    description = stripped
                    break

    parameters = load_schema_json(package_dir)
    return ToolMeta(
        name=name,
        description=description or name,
        kind=kind,
        package_dir=package_dir,
        parameters=parameters,
    )


def to_openai_tool(meta: ToolMeta) -> dict[str, Any]:
    """OpenAI / llama.cpp function-tools entry for one package."""
    return {
        "type": "function",
        "function": {
            "name": meta.name,
            "description": meta.description,
            "parameters": meta.parameters,
        },
    }
