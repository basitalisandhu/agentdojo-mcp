from __future__ import annotations

import copy
import json

import pytest

from agentdojo_mcp.report import render
from agentdojo_mcp.results import Rate, ResultsError, check, load, summarise, tool_usage

from .conftest import FIXTURES

RESULTS = FIXTURES / "fixture-results.json"


@pytest.fixture
def data():
    return load(RESULTS)


def test_summary_counts_come_from_the_file(data):
    s = summarise(data)
    assert (s.utility.hits, s.utility.total) == (3, 3)
    assert (s.attack_success.hits, s.attack_success.total) == (2, 3)
    assert (s.injection_tasks_solvable.hits, s.injection_tasks_solvable.total) == (1, 1)
    assert s.calls == len(data["calls"]) and s.call_errors == 0
    assert s.result_bytes == sum(c["result_bytes"] for c in data["calls"])


def test_rate_formatting():
    assert str(Rate(2, 3)) == "2/3 (66.7%)"
    assert str(Rate(0, 0)) == "0/0 (n/a)"


def test_text_report(data):
    text = render(data, "text")
    assert "Targeted attack success  2/3 (66.7%)" in text
    assert "user_task_0  yes      1/1           injection_task_0" in text
    assert "read_note -> notes_get" in text
    assert "search_notes  -             local" in text


def test_text_report_truncates_the_call_list(data):
    data = copy.deepcopy(data)
    data["calls"] = data["calls"] * 3
    assert "more in the Markdown report" in render(data, "text")


def test_markdown_report_has_every_call_and_the_definitions(data):
    md = render(data, "markdown")
    assert md.startswith("# agentdojo-mcp report: notes")
    assert "| Targeted attack success | 2/3 (66.7%) |" in md
    assert md.count("\n| ") >= len(data["calls"])
    assert "AgentDojo's `security` result" in md
    assert '`{"name": "groceries"}`' in md


def test_markdown_escapes_pipes_in_errors(data):
    data = copy.deepcopy(data)
    data["calls"][1].update(is_error=True, error="a|b")
    assert "a\\|b" in render(data, "markdown")


def test_json_summary(data):
    doc = json.loads(render(data, "json"))
    assert doc["attack_success"] == {"hits": 2, "total": 3}
    assert doc["calls"] == len(data["calls"])


def test_tool_usage_lists_mapped_tools_with_zero_calls(data):
    rows = {r[0]: r for r in tool_usage(data)}
    assert rows["list_notes"][3] == 0 and rows["search_notes"][2] == "local"
    assert rows["(seed)"][1] == "load_state"


@pytest.mark.parametrize(
    "doc, message",
    [
        ([], "JSON object"),
        ({"tool": "other", "format": 1}, "not an agentdojo-mcp"),
        ({"tool": "agentdojo-mcp", "format": 1, "pairs": {}}, "pairs must be a list"),
        ({"tool": "agentdojo-mcp", "format": 1, "pairs": [{}]}, "pairs\\[0\\]"),
        ({"tool": "agentdojo-mcp", "format": 1, "calls": [{"seq": 1}]}, "calls\\[0\\]"),
    ],
)
def test_check_rejects_malformed_results(doc, message):
    with pytest.raises(ResultsError, match=message):
        check(doc)


def test_unreadable_results(tmp_path):
    with pytest.raises(ResultsError, match="cannot read"):
        load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ResultsError, match="not valid JSON"):
        load(bad)
