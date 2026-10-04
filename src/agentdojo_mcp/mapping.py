"""Mapping files: which MCP server tool stands in for each AgentDojo suite tool, and how.

A mapping is a YAML (or JSON) document, version 1::

    version: 1
    suite: banking
    benchmark_version: v1.2.2
    seed:                      # optional: push the suite environment to the server per task
      tool: load_state
      argument: state
    tools:
      get_balance:
        server_tool: account_balance
        mode: server           # server | both | local
        arguments:             # suite parameter -> server parameter
          account: account_id
        constants: {}          # extra server arguments with fixed values
        drop: []               # suite parameters the server does not take
        result: auto           # auto | text | json

The full reference is docs/mapping.md.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import yamlish
from .schema import Param, Surface, ToolSpec, types_compatible, value_type

MAPPING_VERSION = 1
MODES = ("server", "both", "local")
RESULT_MODES = ("auto", "text", "json")
PROPOSE_THRESHOLD = 0.45
PARAM_THRESHOLD = 0.5
# Verb families: a reading verb and a writing verb, or two different writing verbs, mean the
# names describe different actions even when they share a noun.
READ_VERBS = frozenset({"get", "list", "search"})
WRITE_VERBS = frozenset({"send", "create", "delete", "update"})
SEED_NAMES = ("load_state", "seed", "seed_state", "reset_state", "load_fixture", "set_state")

# Verbs that mean the same thing in tool names, folded to one token before comparing.
SYNONYMS = {
    "get": "get",
    "read": "get",
    "fetch": "get",
    "retrieve": "get",
    "show": "get",
    "view": "get",
    "list": "list",
    "all": "list",
    "search": "search",
    "find": "search",
    "query": "search",
    "lookup": "search",
    "send": "send",
    "post": "send",
    "transfer": "send",
    "pay": "send",
    "money": "money",
    "fund": "money",
    "funds": "money",
    "payment": "money",
    "message": "message",
    "msg": "message",
    "create": "create",
    "add": "create",
    "new": "create",
    "insert": "create",
    "make": "create",
    "delete": "delete",
    "remove": "delete",
    "del": "delete",
    "update": "update",
    "edit": "update",
    "modify": "update",
    "set": "update",
    "change": "update",
    "email": "email",
    "emails": "email",
    "mail": "email",
    "mails": "email",
    "file": "file",
    "files": "file",
    "document": "file",
    "documents": "file",
    "doc": "file",
    "docs": "file",
    "event": "event",
    "events": "event",
    "user": "user",
    "users": "user",
    "id": "id",
    "ids": "id",
    "transaction": "transaction",
    "transactions": "transaction",
    "channel": "channel",
    "channels": "channel",
}


class MappingError(ValueError):
    pass


@dataclass
class Seed:
    tool: str
    argument: str = "state"


@dataclass
class ToolMap:
    suite_tool: str
    server_tool: str | None = None
    mode: str = "local"
    arguments: dict[str, str] = field(default_factory=dict)
    constants: dict[str, Any] = field(default_factory=dict)
    drop: list[str] = field(default_factory=list)
    result: str = "auto"
    note: str = ""  # proposal comment, written above the entry; not read back

    def translate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Suite-side keyword arguments to server-side arguments."""
        out: dict[str, Any] = dict(self.constants)
        for key, value in args.items():
            if key in self.drop:
                continue
            out[self.arguments.get(key, key)] = value
        return out


@dataclass
class Mapping:
    suite: str
    tools: dict[str, ToolMap]
    benchmark_version: str | None = None
    seed: Seed | None = None
    version: int = MAPPING_VERSION
    header: list[str] = field(default_factory=list)

    def modes(self) -> dict[str, int]:
        counts = dict.fromkeys(MODES, 0)
        for tm in self.tools.values():
            counts[tm.mode] = counts.get(tm.mode, 0) + 1
        return counts


@dataclass
class Finding:
    level: str  # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level:<7} {self.where}: {self.message}"


# ---------------------------------------------------------------------------------------------
# Reading and writing


def _str_dict(value: Any, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise MappingError(f"{where}: expected a mapping of parameter names to parameter names")
    return {str(k): v for k, v in value.items()}


def from_data(data: Any) -> Mapping:
    """Build a Mapping from parsed YAML/JSON, checking structure only (not against surfaces)."""
    if not isinstance(data, dict):
        raise MappingError("a mapping file must be a YAML or JSON object")
    version = data.get("version")
    if version != MAPPING_VERSION:
        raise MappingError(f"version: expected {MAPPING_VERSION}, got {version!r}")
    suite = data.get("suite")
    if not isinstance(suite, str) or not suite:
        raise MappingError("suite: required, the AgentDojo suite name")
    bv = data.get("benchmark_version")
    if bv is not None and not isinstance(bv, str):
        raise MappingError("benchmark_version: expected a string such as v1.2.2")
    seed = None
    raw_seed = data.get("seed")
    if raw_seed is not None:
        if not isinstance(raw_seed, dict) or not isinstance(raw_seed.get("tool"), str):
            raise MappingError("seed: expected a mapping with 'tool' (and optionally 'argument')")
        argument = raw_seed.get("argument", "state")
        if not isinstance(argument, str) or not argument:
            raise MappingError("seed.argument: expected a parameter name")
        seed = Seed(tool=raw_seed["tool"], argument=argument)
    raw_tools = data.get("tools")
    if not isinstance(raw_tools, dict) or not raw_tools:
        raise MappingError("tools: required, one entry per suite tool")
    tools: dict[str, ToolMap] = {}
    for name, entry in raw_tools.items():
        where = f"tools.{name}"
        if entry is None:
            entry = {}
        if not isinstance(entry, dict):
            raise MappingError(f"{where}: expected a mapping")
        unknown = set(entry) - {
            "server_tool",
            "mode",
            "arguments",
            "constants",
            "drop",
            "result",
        }
        if unknown:
            raise MappingError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        server_tool = entry.get("server_tool")
        if server_tool is not None and not isinstance(server_tool, str):
            raise MappingError(f"{where}.server_tool: expected a tool name or null")
        mode = entry.get("mode", "server" if server_tool else "local")
        if mode not in MODES:
            raise MappingError(f"{where}.mode: expected one of {', '.join(MODES)}, got {mode!r}")
        if mode != "local" and not server_tool:
            raise MappingError(f"{where}: mode {mode} needs a server_tool")
        constants = entry.get("constants") or {}
        if not isinstance(constants, dict):
            raise MappingError(f"{where}.constants: expected a mapping")
        drop = entry.get("drop") or []
        if not isinstance(drop, list) or not all(isinstance(d, str) for d in drop):
            raise MappingError(f"{where}.drop: expected a list of parameter names")
        result = entry.get("result", "auto")
        if result not in RESULT_MODES:
            raise MappingError(f"{where}.result: expected one of {', '.join(RESULT_MODES)}")
        tools[str(name)] = ToolMap(
            suite_tool=str(name),
            server_tool=server_tool,
            mode=mode,
            arguments=_str_dict(entry.get("arguments"), f"{where}.arguments"),
            constants=dict(constants),
            drop=list(drop),
            result=result,
        )
    return Mapping(suite=suite, tools=tools, benchmark_version=bv, seed=seed)


def load(path: Path) -> Mapping:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = yamlish.load(text)
    except yamlish.YamlError as exc:
        raise MappingError(f"{path}: {exc}") from exc
    return from_data(data)


def to_data(mapping: Mapping) -> dict[str, Any]:
    data: dict[str, Any] = {"version": mapping.version, "suite": mapping.suite}
    if mapping.benchmark_version:
        data["benchmark_version"] = mapping.benchmark_version
    if mapping.seed:
        data["seed"] = {"tool": mapping.seed.tool, "argument": mapping.seed.argument}
    tools: dict[str, Any] = {}
    for name, tm in mapping.tools.items():
        tools[name] = {
            "server_tool": tm.server_tool,
            "mode": tm.mode,
            "arguments": dict(tm.arguments),
            "constants": dict(tm.constants),
            "drop": list(tm.drop),
            "result": tm.result,
        }
    data["tools"] = tools
    return data


def dumps(mapping: Mapping) -> str:
    """YAML text with the proposal notes as comments; reads back to the same Mapping."""
    out: list[str] = [f"# {line}" if line else "#" for line in mapping.header]
    if out:
        out.append("")
    out.append(f"version: {mapping.version}")
    out.append(f"suite: {yamlish.scalar(mapping.suite)}")
    if mapping.benchmark_version:
        out.append(f"benchmark_version: {yamlish.scalar(mapping.benchmark_version)}")
    if mapping.seed:
        out.append("seed:")
        out.append(f"  tool: {yamlish.scalar(mapping.seed.tool)}")
        out.append(f"  argument: {yamlish.scalar(mapping.seed.argument)}")
    else:
        out.append("# seed:            # set this if the server can load the suite environment")
        out.append("#   tool: load_state")
        out.append("#   argument: state")
    out.append("tools:")
    for name, tm in mapping.tools.items():
        for line in tm.note.splitlines():
            out.append(f"  # {line}" if line else "  #")
        out.append(f"  {yamlish.scalar(name)}:")
        out.append(f"    server_tool: {yamlish.scalar(tm.server_tool)}")
        out.append(f"    mode: {tm.mode}")
        if tm.arguments:
            out.append("    arguments:")
            for k, v in tm.arguments.items():
                out.append(f"      {yamlish.scalar(k)}: {yamlish.scalar(v)}")
        else:
            out.append("    arguments: {}")
        out.append(f"    constants: {yamlish.scalar(tm.constants)}")
        out.append(f"    drop: {yamlish.scalar(tm.drop)}")
        out.append(f"    result: {tm.result}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------
# Proposals


def tokens(name: str) -> list[str]:
    """Split snake_case, kebab-case, dotted and camelCase names into folded lowercase tokens."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if p]
    out = []
    for p in parts:
        if p in SYNONYMS:
            out.append(SYNONYMS[p])
            continue
        if len(p) > 3 and p.endswith("s") and not p.endswith("ss"):
            p = p[:-1]  # notes -> note, so plural and singular names compare equal
        out.append(SYNONYMS.get(p, p))
    return out


def _verbs_conflict(a: set[str], b: set[str]) -> bool:
    ra, rb, wa, wb = a & READ_VERBS, b & READ_VERBS, a & WRITE_VERBS, b & WRITE_VERBS
    if (ra and wb and not wa) or (wa and rb and not wb):
        return True
    return bool(wa and wb and not wa & wb)


def name_similarity(a: str, b: str) -> float:
    """0..1: Dice overlap of folded tokens blended with a character-level ratio."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    sa, sb = set(ta), set(tb)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb))
    ratio = difflib.SequenceMatcher(None, "_".join(sorted(ta)), "_".join(sorted(tb))).ratio()
    score = 0.7 * dice + 0.3 * ratio
    if _verbs_conflict(sa, sb):
        score *= 0.6  # read_x and delete_x share a noun but do different things
    elif sa & READ_VERBS and sb & READ_VERBS and not sa & sb & READ_VERBS:
        score *= 0.8  # search_x and get_x both read, but take different inputs
    return round(score, 4)


def match_params(suite: ToolSpec, server: ToolSpec) -> tuple[dict[str, str], float]:
    """Pair suite parameters with server parameters; return the pairs and a 0..1 shape score."""
    candidates: list[tuple[float, str, str]] = []
    for sp in suite.params.values():
        for tp in server.params.values():
            if not types_compatible(sp.types, tp.types):
                continue
            score = name_similarity(sp.name, tp.name)
            if score >= PARAM_THRESHOLD:
                candidates.append((score, sp.name, tp.name))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    pairs: dict[str, str] = {}
    used: set[str] = set()
    for _score, s, t in candidates:
        if s in pairs or t in used:
            continue
        pairs[s] = t
        used.add(t)
    # Required parameters left over on both sides, equal in number and pairwise type-compatible,
    # pair up in declaration order even when the names differ (title, body -> name, text). These
    # positional pairs count half towards the score.
    unpaired_suite = [p for p in suite.params.values() if p.name not in pairs and p.required]
    unpaired_server = [p for p in server.params.values() if p.name not in used and p.required]
    positional = 0
    if (
        unpaired_suite
        and len(unpaired_suite) == len(unpaired_server)
        and all(
            types_compatible(a.types, b.types)
            for a, b in zip(unpaired_suite, unpaired_server, strict=True)
        )
    ):
        for a, b in zip(unpaired_suite, unpaired_server, strict=True):
            pairs[a.name] = b.name
            used.add(b.name)
            positional += 1
    if not suite.params and not server.params:
        return pairs, 1.0
    if server.open_params and not server.params:
        return {p: p for p in suite.params}, 0.5
    size = max(len(suite.params), len(server.params))
    coverage = (len(pairs) - positional / 2) / size
    missing_required = [p for p in server.required if p not in used]
    penalty = 0.25 * len(missing_required) / max(1, len(server.required))
    return pairs, max(0.0, round(coverage - penalty, 4))


def score_pair(suite: ToolSpec, server: ToolSpec) -> tuple[float, dict[str, str]]:
    pairs, shape = match_params(suite, server)
    return round(0.65 * name_similarity(suite.name, server.name) + 0.35 * shape, 4), pairs


def propose(
    suite: Surface, server: Surface, suite_name: str, benchmark_version: str | None
) -> Mapping:
    """Best server tool per suite tool by name similarity and schema shape, as an editable draft."""
    seed_tool = next((t for t in server.tools if t in SEED_NAMES), None)
    candidates = {n: t for n, t in server.tools.items() if n != seed_tool}
    tools: dict[str, ToolMap] = {}
    for name in sorted(suite.tools):
        spec = suite.tools[name]
        ranked = sorted(
            ((*score_pair(spec, t), t.name) for t in candidates.values()),
            key=lambda r: (-r[0], r[2]),
        )
        alternatives = ", ".join(f"{n} {s:.2f}" for s, _p, n in ranked[1:3])
        if ranked and ranked[0][0] >= PROPOSE_THRESHOLD:
            score, pairs, target = ranked[0]
            server_spec = candidates[target]
            mode = "both" if spec.mutates else "server"
            drop = sorted(p for p in spec.params if p not in pairs)
            arguments = {k: v for k, v in sorted(pairs.items()) if k != v}
            constants = {p: None for p in server_spec.required if p not in pairs.values()}
            note = f"proposed: {target} (score {score:.2f})"
            if alternatives:
                note += f"; next: {alternatives}"
            if spec.mutates:
                note += "\nchanges suite state: mode both keeps AgentDojo's copy for scoring"
            if constants:
                note += "\nTODO: give values for the server's required parameters in constants"
            tools[name] = ToolMap(
                suite_tool=name,
                server_tool=target,
                mode=mode,
                arguments=arguments,
                constants=constants,
                drop=drop,
                note=note,
            )
        else:
            best = f"; best was {ranked[0][2]} {ranked[0][0]:.2f}" if ranked else ""
            tools[name] = ToolMap(
                suite_tool=name,
                note=f"no server tool scored {PROPOSE_THRESHOLD:.2f} or more{best}; runs locally",
            )
    header = [
        f"agentdojo-mcp mapping for suite {suite_name}"
        + (f" ({benchmark_version})" if benchmark_version else ""),
        f"server: {server.meta.get('name') or server.label} {server.meta.get('version') or ''}".rstrip(),
        "Proposed from tool names and schemas. Review every entry before you run it;",
        "then check it with: agentdojo-mcp validate-mapping",
    ]
    return Mapping(
        suite=suite_name,
        tools=tools,
        benchmark_version=benchmark_version,
        seed=Seed(tool=seed_tool) if seed_tool else None,
        header=header,
    )


# ---------------------------------------------------------------------------------------------
# Validation against both surfaces


def _typename(p: Param) -> str:
    return "|".join(sorted(p.types)) or "any"


def validate(mapping: Mapping, suite: Surface, server: Surface) -> list[Finding]:
    """Every problem that would make a run fail or mislead, as errors and warnings."""
    findings: list[Finding] = []

    def add(level: str, where: str, message: str) -> None:
        findings.append(Finding(level, where, message))

    suite_name = suite.meta.get("suite")
    if suite_name and suite_name != mapping.suite:
        add(
            "error",
            "suite",
            f"mapping is for {mapping.suite!r} but the suite dump is {suite_name!r}",
        )
    bv = suite.meta.get("benchmark_version")
    if mapping.benchmark_version and bv and bv != mapping.benchmark_version:
        add(
            "warning",
            "benchmark_version",
            f"mapping says {mapping.benchmark_version}, suite dump is {bv}",
        )

    for name in suite.tools:
        if name not in mapping.tools:
            add("warning", f"tools.{name}", "suite tool is not in the mapping; it runs locally")
    if mapping.seed:
        seed_spec = server.tools.get(mapping.seed.tool)
        if seed_spec is None:
            add("error", "seed.tool", f"server has no tool {mapping.seed.tool!r}")
        elif seed_spec.params and mapping.seed.argument not in seed_spec.params:
            add(
                "error",
                "seed.argument",
                f"{mapping.seed.tool} has no parameter {mapping.seed.argument!r}",
            )
    elif any(tm.mode == "server" for tm in mapping.tools.values()):
        add(
            "warning",
            "seed",
            "no seed tool: server-mode tools return the server's own data, so AgentDojo's "
            "injections reach the agent only if the server already holds the suite environment",
        )

    for name, tm in mapping.tools.items():
        where = f"tools.{name}"
        spec = suite.tools.get(name)
        if spec is None:
            close = difflib.get_close_matches(name, list(suite.tools), n=1)
            hint = f"; did you mean {close[0]}?" if close else ""
            add("error", where, f"suite has no tool {name!r}{hint}")
            continue
        if tm.mode == "local":
            if tm.server_tool:
                add("warning", where, "mode local ignores server_tool")
            continue
        target = server.tools.get(tm.server_tool or "")
        if target is None:
            close = difflib.get_close_matches(tm.server_tool or "", list(server.tools), n=1)
            hint = f"; did you mean {close[0]}?" if close else ""
            add("error", f"{where}.server_tool", f"server has no tool {tm.server_tool!r}{hint}")
            continue
        if mapping.seed and tm.server_tool == mapping.seed.tool:
            add("error", f"{where}.server_tool", "the seed tool cannot stand in for a suite tool")
        if spec.mutates and tm.mode == "server":
            add(
                "warning",
                f"{where}.mode",
                "this tool changes suite state; in mode server AgentDojo's copy is not updated, "
                "so utility and attack checks that read the environment will not see the change "
                "(use mode both, or a seed tool and a server that reports state back)",
            )
        targets: dict[str, str] = {}
        for src, dst in tm.arguments.items():
            if src not in spec.params:
                add(
                    "error",
                    f"{where}.arguments.{src}",
                    f"suite tool {name} has no parameter {src!r}",
                )
                continue
            if src in tm.drop:
                add("error", f"{where}.arguments.{src}", "parameter is both mapped and dropped")
            if dst in targets:
                add(
                    "error",
                    f"{where}.arguments.{src}",
                    f"server parameter {dst!r} is already fed by {targets[dst]!r}",
                )
            targets[dst] = src
        for d in tm.drop:
            if d not in spec.params:
                add("error", f"{where}.drop", f"suite tool {name} has no parameter {d!r}")
            elif spec.params[d].required:
                add("warning", f"{where}.drop", f"dropping required suite parameter {d!r}")
        for sp in spec.params.values():
            if sp.name in tm.drop:
                continue
            dst = tm.arguments.get(sp.name, sp.name)
            if dst in tm.constants:
                add(
                    "error",
                    f"{where}.constants.{dst}",
                    f"server parameter {dst!r} is set both by a constant and by {sp.name!r}",
                )
            tp = target.params.get(dst)
            if tp is None:
                if not target.open_params:
                    level = "error" if sp.required else "warning"
                    add(
                        level,
                        f"{where}.arguments.{sp.name}",
                        f"{tm.server_tool} has no parameter {dst!r}; map it in arguments or list it in drop",
                    )
                continue
            if not types_compatible(sp.types, tp.types):
                add(
                    "warning",
                    f"{where}.arguments.{sp.name}",
                    f"type {_typename(sp)} feeds {dst!r} of type {_typename(tp)}",
                )
            if tp.required and not sp.required:
                add(
                    "warning",
                    f"{where}.arguments.{sp.name}",
                    f"optional suite parameter feeds required server parameter {dst!r}",
                )
        fed = {tm.arguments.get(p, p) for p in spec.params if p not in tm.drop}
        for key, value in tm.constants.items():
            tp = target.params.get(key)
            if tp is None and not target.open_params:
                add(
                    "error",
                    f"{where}.constants.{key}",
                    f"{tm.server_tool} has no parameter {key!r}",
                )
            elif value is None and tp is not None and tp.required:
                add(
                    "error",
                    f"{where}.constants.{key}",
                    "required server parameter has no value yet",
                )
            elif (
                tp is not None
                and value is not None
                and not types_compatible(frozenset({value_type(value)}), tp.types)
            ):
                add(
                    "warning",
                    f"{where}.constants.{key}",
                    f"value does not match type {_typename(tp)}",
                )
        for req in target.required:
            if req not in fed and req not in tm.constants:
                add(
                    "error",
                    f"{where}",
                    f"{tm.server_tool} requires {req!r}; nothing in the mapping supplies it",
                )
    return findings
