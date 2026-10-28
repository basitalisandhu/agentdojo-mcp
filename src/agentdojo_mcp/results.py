"""The results document ``run`` writes and ``replay`` reads, and the numbers derived from it.

Format 1, a JSON object:

- ``tool``, ``version``, ``format``: who wrote it.
- ``suite``, ``benchmark_version``, ``agentdojo_version``, ``model``, ``attack``.
- ``server``: name, version, protocolVersion, transport, target.
- ``mapping``: file, sha256, and the mode of every suite tool.
- ``user_tasks``: ``[{id, utility}]``, each user task without injections.
- ``injection_tasks``: ``[{id, utility}]``, each injection goal run as a user task (can the
  agent do it at all through this server?).
- ``pairs``: ``[{user_task, injection_task, utility, attack_success}]``, each user task with each
  injection task. ``attack_success`` is AgentDojo's ``security`` value, which is true when the
  injection task's goal was carried out.
- ``calls``: every bridged call, ``{seq, user_task, injection_task, suite_tool, server_tool, mode,
  arguments, result_bytes, is_error, error}``. Results themselves are not stored, only sizes.
- ``notes``: free-text warnings collected during the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESULTS_FORMAT = 1


class ResultsError(ValueError):
    pass


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    @property
    def percent(self) -> str:
        if not self.total:
            return "n/a"
        return f"{100 * self.hits / self.total:.1f}%"

    def __str__(self) -> str:
        return f"{self.hits}/{self.total} ({self.percent})"


def _rate(items: list[dict[str, Any]], key: str) -> Rate:
    return Rate(sum(1 for i in items if i.get(key) is True), len(items))


@dataclass(frozen=True)
class Summary:
    utility: Rate
    utility_under_attack: Rate
    attack_success: Rate
    injection_tasks_solvable: Rate
    calls: int
    call_errors: int
    result_bytes: int


def load(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ResultsError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ResultsError(
            f"{path} is not UTF-8 text (byte 0x{raw[exc.start]:02x} at offset {exc.start})"
        ) from None
    except ValueError as exc:
        raise ResultsError(f"{path} is not valid JSON: {exc}") from exc
    except RecursionError:
        raise ResultsError(f"{path} is not valid JSON: nested too deeply") from None
    check(data, str(path))
    return data


def _optional(value: Any, kind: type | tuple[type, ...]) -> bool:
    return value is None or isinstance(value, kind)


def check(data: Any, label: str = "results") -> None:
    """Check the fields the summary and the reports read, so a hand-edited or truncated file
    is refused with the path of the bad field instead of failing inside the report."""
    if not isinstance(data, dict):
        raise ResultsError(f"{label}: expected a JSON object")
    if data.get("tool") != "agentdojo-mcp" or data.get("format") != RESULTS_FORMAT:
        raise ResultsError(f"{label}: not an agentdojo-mcp results file (format {RESULTS_FORMAT})")
    for key in ("user_tasks", "injection_tasks", "pairs", "calls", "notes"):
        if not isinstance(data.get(key, []), list):
            raise ResultsError(f"{label}: {key} must be a list")
    for key in ("server", "mapping"):
        if not _optional(data.get(key), dict):
            raise ResultsError(f"{label}: {key} must be an object")
    tools = (data.get("mapping") or {}).get("tools")
    if not _optional(tools, dict):
        raise ResultsError(f"{label}: mapping.tools must be an object")
    for name, entry in (tools or {}).items():
        if not isinstance(entry, dict):
            raise ResultsError(f"{label}: mapping.tools.{name} must be an object")
        for field in ("server_tool", "mode"):
            if not _optional(entry.get(field), str):
                raise ResultsError(f"{label}: mapping.tools.{name}.{field} must be a string")
    for key in ("user_tasks", "injection_tasks"):
        for i, task in enumerate(data.get(key, [])):
            if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                raise ResultsError(f"{label}: {key}[{i}] needs a string id")
    for i, pair in enumerate(data.get("pairs", [])):
        if (
            not isinstance(pair, dict)
            or not isinstance(pair.get("user_task"), str)
            or not isinstance(pair.get("injection_task"), str)
        ):
            raise ResultsError(f"{label}: pairs[{i}] needs user_task and injection_task")
    for i, call in enumerate(data.get("calls", [])):
        if not isinstance(call, dict) or not isinstance(call.get("suite_tool"), str):
            raise ResultsError(f"{label}: calls[{i}] needs suite_tool")
        for field in ("server_tool", "mode", "user_task", "injection_task"):
            if not _optional(call.get(field), str):
                raise ResultsError(f"{label}: calls[{i}].{field} must be a string")
        size = call.get("result_bytes")
        if not _optional(size, int) or isinstance(size, bool):
            raise ResultsError(f"{label}: calls[{i}].result_bytes must be an integer")


def summarise(data: dict[str, Any]) -> Summary:
    calls = data.get("calls", [])
    return Summary(
        utility=_rate(data.get("user_tasks", []), "utility"),
        utility_under_attack=_rate(data.get("pairs", []), "utility"),
        attack_success=_rate(data.get("pairs", []), "attack_success"),
        injection_tasks_solvable=_rate(data.get("injection_tasks", []), "utility"),
        calls=len(calls),
        call_errors=sum(1 for c in calls if c.get("is_error")),
        result_bytes=sum(int(c.get("result_bytes") or 0) for c in calls),
    )


def tool_usage(data: dict[str, Any]) -> list[tuple[str, str, str, int, int]]:
    """(suite tool, server tool, mode, calls, errors) for every mapped tool and every called tool."""
    modes: dict[str, dict[str, Any]] = data.get("mapping", {}).get("tools", {}) or {}
    rows: dict[str, list[Any]] = {
        name: [entry.get("server_tool") or "-", entry.get("mode", "local"), 0, 0]
        for name, entry in modes.items()
    }
    for call in data.get("calls", []):
        row = rows.setdefault(
            call["suite_tool"], [call.get("server_tool") or "-", call.get("mode", "?"), 0, 0]
        )
        row[2] += 1
        row[3] += 1 if call.get("is_error") else 0
    return [(name, *rows[name]) for name in sorted(rows)]  # type: ignore[misc]


def dump(data: dict[str, Any], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ResultsError(f"cannot write {path}: {exc.strerror or exc}") from None


# ---------------------------------------------------------------------------------------------
# Comparing two runs (replay --diff)

_RATES = (
    ("utility", "Utility, no attack"),
    ("utility_under_attack", "Utility under attack"),
    ("attack_success", "Targeted attack success"),
    ("injection_tasks_solvable", "Injection goals doable"),
)


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What changed from ``old`` to ``new``: headline rates, and every user task and pair whose
    utility or attack success changed. Both documents must have passed :func:`check`.

    A regression is a user task or pair that lost utility, or a pair whose attack now succeeds.
    Tasks present in only one run are listed but are not regressions.
    """
    so, sn = summarise(old), summarise(new)
    rates = [
        {
            "measure": label,
            "old": {"hits": getattr(so, key).hits, "total": getattr(so, key).total},
            "new": {"hits": getattr(sn, key).hits, "total": getattr(sn, key).total},
        }
        for key, label in _RATES
    ]
    tasks: list[dict[str, Any]] = []
    old_tasks = {t["id"]: _flag(t.get("utility")) for t in old.get("user_tasks", [])}
    new_tasks = {t["id"]: _flag(t.get("utility")) for t in new.get("user_tasks", [])}
    for tid in sorted(old_tasks.keys() | new_tasks.keys()):
        before, after = old_tasks.get(tid, "absent"), new_tasks.get(tid, "absent")
        if before != after:
            tasks.append(
                {
                    "user_task": tid,
                    "utility": [before, after],
                    "regression": before is True and after is not True and after != "absent",
                }
            )
    pairs: list[dict[str, Any]] = []

    def by_pair(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(p["user_task"], p["injection_task"]): p for p in doc.get("pairs", [])}

    old_pairs, new_pairs = by_pair(old), by_pair(new)
    for key in sorted(old_pairs.keys() | new_pairs.keys()):
        po, pn = old_pairs.get(key), new_pairs.get(key)
        if po is None or pn is None:
            pairs.append(
                {
                    "user_task": key[0],
                    "injection_task": key[1],
                    "only_in": "old" if pn is None else "new",
                    "regression": False,
                }
            )
            continue
        u = [_flag(po.get("utility")), _flag(pn.get("utility"))]
        a = [_flag(po.get("attack_success")), _flag(pn.get("attack_success"))]
        if u[0] == u[1] and a[0] == a[1]:
            continue
        pairs.append(
            {
                "user_task": key[0],
                "injection_task": key[1],
                "utility": u,
                "attack_success": a,
                "regression": (u[0] is True and u[1] is False)
                or (a[0] is not True and a[1] is True),
            }
        )
    notes = []
    for field in ("suite", "benchmark_version", "attack", "model"):
        if old.get(field) != new.get(field):
            notes.append(f"{field} differs: {old.get(field)!r} -> {new.get(field)!r}")
    return {
        "rates": rates,
        "user_tasks": tasks,
        "pairs": pairs,
        "notes": notes,
        "regressions": sum(1 for x in tasks + pairs if x["regression"]),
    }
