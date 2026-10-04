"""Render a results document as terminal text, Markdown or a JSON summary."""

from __future__ import annotations

import json
from typing import Any

from .results import Summary, summarise, tool_usage

FORMATS = ("text", "markdown", "json")
ARG_WIDTH = 60


def _yes(value: Any) -> str:
    return "yes" if value is True else "no" if value is False else "-"


def _args(arguments: Any, width: int = ARG_WIDTH) -> str:
    text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    return text if len(text) <= width else text[: width - 3] + "..."


def _task(call: dict[str, Any]) -> str:
    ut = call.get("user_task") or "-"
    it = call.get("injection_task")
    return f"{ut} + {it}" if it else ut


def _header(data: dict[str, Any]) -> list[tuple[str, str]]:
    server = data.get("server", {}) or {}
    mapping = data.get("mapping", {}) or {}
    modes: dict[str, int] = {}
    for entry in (mapping.get("tools") or {}).values():
        modes[entry.get("mode", "local")] = modes.get(entry.get("mode", "local"), 0) + 1
    mode_text = ", ".join(f"{n} {m}" for m, n in sorted(modes.items())) or "-"
    srv = " ".join(x for x in (server.get("name"), server.get("version")) if x) or "-"
    return [
        ("Suite", f"{data.get('suite', '-')} ({data.get('benchmark_version') or '-'})"),
        ("Server", f"{srv} over {server.get('transport') or '-'}"),
        ("Mapping", f"{mapping.get('file') or '-'} ({mode_text})"),
        ("Model", str(data.get("model") or "-")),
        ("Attack", str(data.get("attack") or "none")),
        ("AgentDojo", str(data.get("agentdojo_version") or "-")),
    ]


def _headline(s: Summary) -> list[tuple[str, str]]:
    return [
        ("Utility, no attack", str(s.utility)),
        ("Utility under attack", str(s.utility_under_attack)),
        ("Targeted attack success", str(s.attack_success)),
        ("Injection goals doable", str(s.injection_tasks_solvable)),
        ("MCP calls", f"{s.calls} ({s.call_errors} errors, {s.result_bytes} result bytes)"),
    ]


def render_text(data: dict[str, Any], max_calls: int = 20) -> str:
    s = summarise(data)
    out: list[str] = ["agentdojo-mcp results", ""]
    rows = [*_header(data), ("", ""), *_headline(s)]
    width = max(len(k) for k, _ in rows)
    out += [f"  {k:<{width}}  {v}".rstrip() if k else "" for k, v in rows]
    out += ["", "Per user task"]
    pairs_by_task: dict[str, list[dict[str, Any]]] = {}
    for p in data.get("pairs", []):
        pairs_by_task.setdefault(p["user_task"], []).append(p)
    tasks = data.get("user_tasks", [])
    tw = max([len(t["id"]) for t in tasks] + [9])
    out.append(f"  {'task':<{tw}}  utility  under attack  attacks succeeded")
    for t in tasks:
        pairs = pairs_by_task.get(t["id"], [])
        under = f"{sum(1 for p in pairs if p.get('utility'))}/{len(pairs)}" if pairs else "-"
        hit = [p["injection_task"] for p in pairs if p.get("attack_success")]
        out.append(
            f"  {t['id']:<{tw}}  {_yes(t.get('utility')):<7}  {under:<12}  "
            f"{', '.join(hit) if hit else ('none' if pairs else '-')}"
        )
    out += ["", "Tools"]
    usage = tool_usage(data)
    if usage:
        sw = max(len(u[0]) for u in usage)
        vw = max(len(u[1]) for u in usage)
        out.append(f"  {'suite tool':<{sw}}  {'server tool':<{vw}}  mode    calls  errors")
        for name, server_tool, mode, calls, errors in usage:
            out.append(f"  {name:<{sw}}  {server_tool:<{vw}}  {mode:<6}  {calls:>5}  {errors:>6}")
    calls = data.get("calls", [])
    out += ["", f"Calls ({len(calls)})"]
    for call in calls[:max_calls]:
        arrow = f"{call['suite_tool']} -> {call.get('server_tool') or '-'}"
        err = "  ERROR" if call.get("is_error") else ""
        out.append(
            f"  #{call.get('seq', '?'):<3} {_task(call)}  {arrow}  {_args(call.get('arguments', {}), 44)}"
            f"  {call.get('result_bytes', 0)} B{err}"
        )
    if len(calls) > max_calls:
        out.append(f"  ... {len(calls) - max_calls} more in the Markdown report")
    for note in data.get("notes", []):
        out.append(f"note: {note}")
    return "\n".join(out) + "\n"


def _md_cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict[str, Any]) -> str:
    s = summarise(data)
    out = [f"# agentdojo-mcp report: {data.get('suite', '-')}", ""]
    out += ["| | |", "|---|---|"]
    out += [f"| {k} | {_md_cell(v)} |" for k, v in _header(data)]
    out += ["", "## Results", "", "| Measure | Value |", "|---|---|"]
    out += [f"| {k} | {_md_cell(v)} |" for k, v in _headline(s)]
    out += [
        "",
        "Targeted attack success is AgentDojo's `security` result: the share of (user task, "
        "injection task) pairs in which the injection task's goal was carried out. "
        '"Injection goals doable" runs each injection goal as an ordinary user task; a goal '
        "the agent cannot reach through this server cannot succeed as an attack either.",
        "",
        "## Per task",
        "",
        "| User task | Utility | Injection task | Utility under attack | Attack succeeded |",
        "|---|---|---|---|---|",
    ]
    pairs_by_task: dict[str, list[dict[str, Any]]] = {}
    for p in data.get("pairs", []):
        pairs_by_task.setdefault(p["user_task"], []).append(p)
    for t in data.get("user_tasks", []):
        pairs = pairs_by_task.get(t["id"]) or [{}]
        for i, p in enumerate(pairs):
            first = i == 0
            out.append(
                f"| {t['id'] if first else ''} | {_yes(t.get('utility')) if first else ''} | "
                f"{p.get('injection_task', '-')} | {_yes(p.get('utility'))} | "
                f"{_yes(p.get('attack_success'))} |"
            )
    if data.get("injection_tasks"):
        out += ["", "| Injection task run as a user task | Utility |", "|---|---|"]
        out += [f"| {t['id']} | {_yes(t.get('utility'))} |" for t in data["injection_tasks"]]
    out += [
        "",
        "## Tools",
        "",
        "| Suite tool | Server tool | Mode | Calls | Errors |",
        "|---|---|---|---|---|",
    ]
    out += [f"| `{n}` | `{t}` | {m} | {c} | {e} |" for n, t, m, c, e in tool_usage(data)]
    calls = data.get("calls", [])
    out += [
        "",
        f"## Calls ({len(calls)})",
        "",
        "| # | Task | Suite tool | Server tool | Mode | Arguments | Result bytes | Error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in calls:
        err = _md_cell(c.get("error") or "yes") if c.get("is_error") else ""
        out.append(
            f"| {c.get('seq', '')} | {_md_cell(_task(c))} | `{c['suite_tool']}` | "
            f"`{c.get('server_tool') or '-'}` | {c.get('mode', '')} | "
            f"`{_md_cell(_args(c.get('arguments', {}), 200))}` | {c.get('result_bytes', 0)} | {err} |"
        )
    if data.get("notes"):
        out += ["", "## Notes", ""]
        out += [f"- {_md_cell(n)}" for n in data["notes"]]
    return "\n".join(out) + "\n"


def render_json(data: dict[str, Any]) -> str:
    s = summarise(data)
    doc = {
        "suite": data.get("suite"),
        "model": data.get("model"),
        "attack": data.get("attack"),
        "utility": {"hits": s.utility.hits, "total": s.utility.total},
        "utility_under_attack": {
            "hits": s.utility_under_attack.hits,
            "total": s.utility_under_attack.total,
        },
        "attack_success": {"hits": s.attack_success.hits, "total": s.attack_success.total},
        "injection_tasks_solvable": {
            "hits": s.injection_tasks_solvable.hits,
            "total": s.injection_tasks_solvable.total,
        },
        "calls": s.calls,
        "call_errors": s.call_errors,
        "result_bytes": s.result_bytes,
    }
    return json.dumps(doc, indent=2) + "\n"


def render(data: dict[str, Any], fmt: str) -> str:
    if fmt == "markdown":
        return render_markdown(data)
    if fmt == "json":
        return render_json(data)
    return render_text(data)
