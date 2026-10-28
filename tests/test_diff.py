"""replay --diff: what changed between two results files, and the regression exit code."""

from __future__ import annotations

import copy
import json

import pytest

from agentdojo_mcp.cli import main
from agentdojo_mcp.report import render_diff
from agentdojo_mcp.results import diff, load

from .conftest import FIXTURES

RESULTS = FIXTURES / "fixture-results.json"


@pytest.fixture
def old():
    return load(RESULTS)


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_identical_files_have_no_changes(old):
    d = diff(old, copy.deepcopy(old))
    assert d["changes"] == [] and d["added"] == [] and d["removed"] == []
    assert d["regressed"] is False
    assert "Changed (0)" in render_diff(d, "new", "old")


def test_lower_attack_success_is_an_improvement(old):
    new = copy.deepcopy(old)
    new["pairs"][0]["attack_success"] = False
    d = diff(new, old)
    assert d["regressed"] is False
    assert d["changes"] == [
        {
            "kind": "pair",
            "id": "user_task_0 + injection_task_0",
            "field": "attack_success",
            "old": True,
            "new": False,
        }
    ]
    assert d["rates"]["attack_success"]["old"] == "2/3 (66.7%)"
    assert d["rates"]["attack_success"]["new"] == "1/3 (33.3%)"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["user_tasks"][0].__setitem__("utility", False),
        lambda d: d["pairs"][0].__setitem__("utility", False),
    ],
)
def test_lower_utility_is_a_regression(old, mutate):
    new = copy.deepcopy(old)
    mutate(new)
    assert diff(new, old)["regressed"] is True


def test_higher_attack_success_is_a_regression(old):
    new, before = copy.deepcopy(old), copy.deepcopy(old)
    before["pairs"][0]["attack_success"] = False
    assert diff(new, before)["regressed"] is True


def test_added_and_removed_tasks(old):
    new = copy.deepcopy(old)
    new["user_tasks"].append({"id": "user_task_9", "utility": True})
    removed = new["injection_tasks"].pop()
    d = diff(new, old)
    assert "user task user_task_9" in d["added"]
    assert f"injection task {removed['id']}" in d["removed"]
    text = render_diff(d, "new", "old")
    assert "Only in the new file\n  user task user_task_9" in text


def test_cli_exit_codes_and_formats(old, tmp_path, capsys):
    worse = copy.deepcopy(old)
    worse["user_tasks"][1]["utility"] = False
    new_path, old_path = write(tmp_path, "new.json", worse), str(RESULTS)
    assert main(["replay", new_path, "--diff", old_path]) == 1
    out = capsys.readouterr().out
    assert "user task user_task_1: utility yes -> no" in out
    assert "Utility, no attack       3/3 (100.0%) -> 2/3 (66.7%)  (changed)" in out
    assert out.rstrip().endswith("regressed: attack success went up or utility went down")
    assert main(["replay", old_path, "--diff", new_path]) == 0
    capsys.readouterr()
    assert main(["replay", new_path, "--diff", old_path, "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["regressed"] is True
    md = tmp_path / "diff.md"
    assert (
        main(["replay", new_path, "--diff", old_path, "--format", "markdown", "-o", str(md)]) == 1
    )
    assert "| Utility, no attack | 3/3 (100.0%) | 2/3 (66.7%) |" in md.read_text(encoding="utf-8")


def test_cli_diff_with_a_bad_old_file_exits_2(tmp_path, capsys):
    assert main(["replay", str(RESULTS), "--diff", str(tmp_path / "missing.json")]) == 2
    assert "cannot read" in capsys.readouterr().err
