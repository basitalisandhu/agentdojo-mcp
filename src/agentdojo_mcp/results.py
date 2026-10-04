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
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResultsError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise ResultsError(f"{path} is not valid JSON: {exc}") from exc
    check(data, str(path))
    return data


def check(data: Any, label: str = "results") -> None:
    if not isinstance(data, dict):
        raise ResultsError(f"{label}: expected a JSON object")
    if data.get("tool") != "agentdojo-mcp" or data.get("format") != RESULTS_FORMAT:
        raise ResultsError(f"{label}: not an agentdojo-mcp results file (format {RESULTS_FORMAT})")
    for key in ("user_tasks", "injection_tasks", "pairs", "calls"):
        if not isinstance(data.get(key, []), list):
            raise ResultsError(f"{label}: {key} must be a list")
    for i, pair in enumerate(data.get("pairs", [])):
        if not isinstance(pair, dict) or "user_task" not in pair or "injection_task" not in pair:
            raise ResultsError(f"{label}: pairs[{i}] needs user_task and injection_task")
    for i, call in enumerate(data.get("calls", [])):
        if not isinstance(call, dict) or "suite_tool" not in call:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
