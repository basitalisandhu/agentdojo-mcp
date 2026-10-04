"""Tool surfaces on both sides of a mapping: an AgentDojo suite dump and an MCP server dump.

A suite dump is what ``agentdojo-mcp dump-suite`` writes (it needs AgentDojo). A server dump is
what ``agentdojo-mcp inspect --json`` writes; a bare ``tools/list`` result or a JSON list of tools
is accepted too. Both reduce to :class:`ToolSpec`, so mapping and validation run offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUITE_DUMP_FORMAT = "agentdojo-mcp/suite-dump"


class DumpError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    types: frozenset[str]
    required: bool
    description: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    params: dict[str, Param] = field(default_factory=dict)
    open_params: bool = False  # the schema declares no properties, so any argument is accepted
    mutates: bool | None = (
        None  # suite tools only: changed the environment in a ground-truth replay
    )

    @property
    def required(self) -> list[str]:
        return [p.name for p in self.params.values() if p.required]


@dataclass
class Surface:
    """One side of a mapping: a named set of tools."""

    label: str
    tools: dict[str, ToolSpec]
    meta: dict[str, Any] = field(default_factory=dict)


def schema_types(node: Any) -> frozenset[str]:
    """JSON Schema types a property accepts; empty when the schema does not say."""
    if not isinstance(node, dict):
        return frozenset()
    t = node.get("type")
    if isinstance(t, str):
        return frozenset({t})
    if isinstance(t, list):
        return frozenset(x for x in t if isinstance(x, str))
    out: set[str] = set()
    for key in ("anyOf", "oneOf"):
        for sub in node.get(key) or []:
            out |= schema_types(sub)
    if "enum" in node and not out:
        values = node["enum"]
        if isinstance(values, list):
            for v in values:
                out.add(_json_type(v))
    return frozenset(out)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def value_type(value: Any) -> str:
    return _json_type(value)


def types_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """True when the two type sets can hold the same value (unknown matches anything)."""
    a, b = a - {"null"}, b - {"null"}
    if not a or not b:
        return True
    widen = {"integer": {"integer", "number"}, "number": {"number", "integer"}}
    return any(widen.get(t, {t}) & b for t in a)


def tool_from_schema(raw: dict[str, Any], mutates: bool | None = None) -> ToolSpec:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise DumpError("a tool has no name")
    schema = raw.get("inputSchema")
    if schema is None:
        schema = raw.get("input_schema") or {}
    if not isinstance(schema, dict):
        raise DumpError(f"tool {name}: inputSchema is not an object")
    props = schema.get("properties")
    required = schema.get("required") or []
    params: dict[str, Param] = {}
    if isinstance(props, dict):
        for pname, pschema in props.items():
            desc = pschema.get("description", "") if isinstance(pschema, dict) else ""
            params[pname] = Param(
                name=pname,
                types=schema_types(pschema),
                required=pname in required,
                description=str(desc or ""),
            )
    open_params = not isinstance(props, dict) or (
        not props and schema.get("additionalProperties") is not False
    )
    return ToolSpec(
        name=name,
        description=str(raw.get("description") or ""),
        params=params,
        open_params=open_params,
        mutates=mutates,
    )


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DumpError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise DumpError(f"{path} is not valid JSON: {exc}") from exc


def server_surface(data: Any, label: str = "server") -> Surface:
    if isinstance(data, list):
        raw_tools, meta = data, {}
    elif isinstance(data, dict) and isinstance(data.get("tools"), list):
        raw_tools = data["tools"]
        meta = data.get("server") if isinstance(data.get("server"), dict) else {}
    else:
        raise DumpError(f"{label}: expected a server dump with a 'tools' array")
    tools: dict[str, ToolSpec] = {}
    for raw in raw_tools:
        if not isinstance(raw, dict):
            raise DumpError(f"{label}: a tool entry is not an object")
        spec = tool_from_schema(raw)
        if spec.name in tools:
            raise DumpError(f"{label}: duplicate tool name {spec.name}")
        tools[spec.name] = spec
    return Surface(label=label, tools=tools, meta=dict(meta))


def suite_surface(data: Any, label: str = "suite") -> Surface:
    if not isinstance(data, dict) or data.get("format") != SUITE_DUMP_FORMAT:
        raise DumpError(f"{label}: not a suite dump (format must be {SUITE_DUMP_FORMAT!r})")
    raw_tools = data.get("tools")
    if not isinstance(raw_tools, list):
        raise DumpError(f"{label}: suite dump has no 'tools' array")
    tools: dict[str, ToolSpec] = {}
    for raw in raw_tools:
        if not isinstance(raw, dict):
            raise DumpError(f"{label}: a tool entry is not an object")
        mutates = raw.get("mutates")
        spec = tool_from_schema(raw, mutates if isinstance(mutates, bool) else None)
        tools[spec.name] = spec
    meta = {k: data.get(k) for k in ("suite", "benchmark_version", "agentdojo_version")}
    return Surface(label=label, tools=tools, meta=meta)


def load_server_dump(path: Path) -> Surface:
    return server_surface(read_json(path), label=str(path))


def load_suite_dump(path: Path) -> Surface:
    return suite_surface(read_json(path), label=str(path))
