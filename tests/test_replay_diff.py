"""replay NEW --diff OLD: changed tasks, changed rates and the regression exit code."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agentdojo_mcp.cli import main

BASE: dict[str, Any] = {
    "tool": "agentdojo-mcp",
    "format": 1,
    "suite": "notes",
    "attack": "direct",
    "user_tasks": [{"id": "u0", "utility": True}, {"id": "u1", "utility": True}],
    "injection_tasks": [{"id": "i0", "utility": True}],
    "pairs": [
        {"user_task": "u0", "injection_task": "i0", "utility": True, "attack_success": False},
        {"user_task": "u1", "injection_task": "i0", "utility": True, "attack_success": False},
    ],
    "calls": [],
}


def _pair(tmp_path: Path, change) -> tuple[Path, Path]:
    new = copy.deepcopy(BASE)
    change(new)
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    old_path.write_text(json.dumps(BASE), encoding="utf-8")
    new_path.write_text(json.dumps(new), encoding="utf-8")
    return old_path, new_path


def _run(capsys, old: Path, new: Path, *extra: str) -> tuple[int, str]:
    rc = main(["replay", str(new), "--diff", str(old), *extra])
    return rc, capsys.readouterr().out


def test_no_change(tmp_path, capsys):
    old, new = _pair(tmp_path, lambda d: None)
    rc, out = _run(capsys, old, new)
    assert rc == 0
    assert "Changed tasks\n  none" in out
    assert "Targeted attack success  0/2 (0.0%) -> 0/2 (0.0%)" in out
    assert out.endswith("0 regression(s)\n")


def test_lost_utility_is_a_regression(tmp_path, capsys):
    old, new = _pair(tmp_path, lambda d: d["user_tasks"][1].update(utility=False))
    rc, out = _run(capsys, old, new)
    assert rc == 1
    assert "REGRESSION u1: utility yes -> no" in out
    assert "Utility, no attack       2/2 (100.0%) -> 1/2 (50.0%)" in out


def test_new_attack_success_is_a_regression(tmp_path, capsys):
    old, new = _pair(tmp_path, lambda d: d["pairs"][0].update(attack_success=True))
    rc, out = _run(capsys, old, new)
    assert rc == 1
    assert "REGRESSION u0 + i0: attack succeeded no -> yes" in out


def test_improvement_only_exits_0(tmp_path, capsys):
    old, new = _pair(tmp_path, lambda d: None)  # new is BASE; the old run had a successful attack
    data = json.loads(old.read_text(encoding="utf-8"))
    data["pairs"][1]["attack_success"] = True
    old.write_text(json.dumps(data), encoding="utf-8")
    rc, out = _run(capsys, old, new)
    assert rc == 0
    assert "  u1 + i0: attack succeeded yes -> no" in out


def test_tasks_in_one_run_only_are_listed_not_regressions(tmp_path, capsys):
    def add(d):
        d["user_tasks"].append({"id": "u2", "utility": False})
        d["pairs"].append(
            {"user_task": "u2", "injection_task": "i0", "utility": False, "attack_success": True}
        )
        d["suite"] = "notes-v2"

    old, new = _pair(tmp_path, add)
    rc, out = _run(capsys, old, new)
    assert rc == 0
    assert "u2: utility absent -> no" in out
    assert "u2 + i0: only in the new run" in out
    assert "note: suite differs: 'notes' -> 'notes-v2'" in out


def test_json_and_markdown(tmp_path, capsys):
    old, new = _pair(tmp_path, lambda d: d["pairs"][0].update(utility=False))
    rc, out = _run(capsys, old, new, "--format", "json")
    doc = json.loads(out)
    assert rc == 1
    assert set(doc) == {"rates", "user_tasks", "pairs", "notes", "regressions"}
    assert doc["pairs"] == [
        {
            "user_task": "u0",
            "injection_task": "i0",
            "utility": [True, False],
            "attack_success": [False, False],
            "regression": True,
        }
    ]
    rc, out = _run(capsys, old, new, "--format", "markdown")
    assert out.startswith("# agentdojo-mcp diff\n")
    assert "- REGRESSION u0 + i0: utility yes -> no" in out


@pytest.mark.parametrize("content", ["not json", '{"tool": "other"}'])
def test_bad_old_file_exits_2(tmp_path, capsys, content):
    old, new = _pair(tmp_path, lambda d: None)
    old.write_text(content, encoding="utf-8")
    assert main(["replay", str(new), "--diff", str(old)]) == 2
    assert "agentdojo-mcp: error:" in capsys.readouterr().err
