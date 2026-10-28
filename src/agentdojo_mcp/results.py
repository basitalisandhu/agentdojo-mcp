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

from . import _jsondepth

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


def _int(value: Any) -> int:
    """A count from a results file; anything that is not a whole number counts as 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
        data = _jsondepth.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResultsError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except RecursionError:
        raise ResultsError(f"{path} is not valid JSON: nested too deeply") from None
    except ValueError as exc:
        raise ResultsError(f"{path} is not valid JSON: {exc}") from exc
    check(data, str(path))
    return data


def check(data: Any, label: str = "results") -> None:
    if not isinstance(data, dict):
        raise ResultsError(f"{label}: expected a JSON object")
    if data.get("tool") != "agentdojo-mcp" or data.get("format") != RESULTS_FORMAT:
        raise ResultsError(f"{label}: not an agentdojo-mcp results file (format {RESULTS_FORMAT})")
    for key in ("user_tasks", "injection_tasks", "pairs", "calls", "notes"):
        if not isinstance(data.get(key, []), list):
            raise ResultsError(f"{label}: {key} must be a list")
    for key in ("server", "mapping"):
        if not isinstance(data.get(key) or {}, dict):
            raise ResultsError(f"{label}: {key} must be an object")
    tools = (data.get("mapping") or {}).get("tools") or {}
    if not isinstance(tools, dict) or not all(isinstance(e, dict) for e in tools.values()):
        raise ResultsError(f"{label}: mapping.tools must map tool names to objects")
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


def summarise(data: dict[str, Any]) -> Summary:
    calls = data.get("calls", [])
    return Summary(
        utility=_rate(data.get("user_tasks", []), "utility"),
        utility_under_attack=_rate(data.get("pairs", []), "utility"),
        attack_success=_rate(data.get("pairs", []), "attack_success"),
        injection_tasks_solvable=_rate(data.get("injection_tasks", []), "utility"),
        calls=len(calls),
        call_errors=sum(1 for c in calls if c.get("is_error")),
        result_bytes=sum(_int(c.get("result_bytes")) for c in calls),
    )


def tool_usage(data: dict[str, Any]) -> list[tuple[str, str, str, int, int]]:
    """(suite tool, server tool, mode, calls, errors) for every mapped tool and every called tool."""
    modes: dict[str, dict[str, Any]] = (data.get("mapping") or {}).get("tools") or {}
    rows: dict[str, list[Any]] = {
        str(name): [str(entry.get("server_tool") or "-"), str(entry.get("mode", "local")), 0, 0]
        for name, entry in modes.items()
    }
    for call in data.get("calls", []):
        row = rows.setdefault(
            call["suite_tool"],
            [str(call.get("server_tool") or "-"), str(call.get("mode", "?")), 0, 0],
        )
        row[2] += 1
        row[3] += 1 if call.get("is_error") else 0
    return [(name, *rows[name]) for name in sorted(rows)]  # type: ignore[misc]


def dump(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fraction(rate: Rate) -> float | None:
    return rate.hits / rate.total if rate.total else None


def diff(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """What changed from ``old`` to ``new``: each headline rate, and every user task, injection
    task and (user task, injection task) pair whose utility or attack success differs.

    ``regressed`` is true when targeted attack success went up, or utility (without or under
    attack) went down. Tasks present in only one file are listed under ``added`` and ``removed``.
    """
    s_new, s_old = summarise(new), summarise(old)
    rates = {}
    for key in ("utility", "utility_under_attack", "attack_success", "injection_tasks_solvable"):
        a, b = getattr(s_old, key), getattr(s_new, key)
        rates[key] = {
            "old": str(a),
            "new": str(b),
            "old_value": _fraction(a),
            "new_value": _fraction(b),
        }

    def worse(key: str, higher_is_worse: bool) -> bool:
        a, b = rates[key]["old_value"], rates[key]["new_value"]
        if a is None or b is None:
            return False
        return b > a if higher_is_worse else b < a

    changes: list[dict[str, Any]] = []
    added: list[str] = []
    removed: list[str] = []

    def compare(kind: str, old_items: dict[Any, dict], new_items: dict[Any, dict], fields) -> None:
        for key, item in new_items.items():
            label = key if isinstance(key, str) else " + ".join(key)
            before = old_items.get(key)
            if before is None:
                added.append(f"{kind} {label}")
                continue
            for field in fields:
                if before.get(field) != item.get(field):
                    changes.append(
                        {
                            "kind": kind,
                            "id": label,
                            "field": field,
                            "old": before.get(field),
                            "new": item.get(field),
                        }
                    )
        removed.extend(
            f"{kind} {k if isinstance(k, str) else ' + '.join(k)}"
            for k in old_items
            if k not in new_items
        )

    def by_id(data: dict[str, Any], key: str) -> dict[str, dict]:
        return {t["id"]: t for t in data.get(key, [])}

    def by_pair(data: dict[str, Any]) -> dict[tuple[str, str], dict]:
        return {(p["user_task"], p["injection_task"]): p for p in data.get("pairs", [])}

    compare("user task", by_id(old, "user_tasks"), by_id(new, "user_tasks"), ("utility",))
    compare(
        "injection task",
        by_id(old, "injection_tasks"),
        by_id(new, "injection_tasks"),
        ("utility",),
    )
    compare("pair", by_pair(old), by_pair(new), ("utility", "attack_success"))
    regressed = (
        worse("attack_success", True)
        or worse("utility", False)
        or worse("utility_under_attack", False)
    )
    return {
        "rates": rates,
        "changes": changes,
        "added": added,
        "removed": removed,
        "regressed": regressed,
    }
