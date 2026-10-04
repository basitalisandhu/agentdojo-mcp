"""Integration with AgentDojo itself. Marked ``dojo``: skipped unless AgentDojo is installed.

These run the fixture suite through the bridge against the fixture MCP server, with AgentDojo's
own benchmark functions and no model and no network: the ground-truth pipeline, and a scripted
test double that carries out instructions it reads in tool results.
"""

from __future__ import annotations

import json
import sys

import pytest

from agentdojo_mcp.cli import main

from .conftest import DOJO_SUITE, FIXTURES, ROOT, server_cmd

pytestmark = pytest.mark.dojo

VOLATILE = ("agentdojo_version",)


def _normalise(data: dict) -> dict:
    data = json.loads(json.dumps(data))
    for key in VOLATILE:
        data.pop(key, None)
    data["server"].pop("target", None)
    data["mapping"].pop("sha256", None)
    for call in data["calls"]:
        call["ms"] = 0
    return data


def test_bridge_reproduces_the_committed_fixture_results():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import make_fixture_results
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    fresh = make_fixture_results.generate()
    committed = json.loads((FIXTURES / "fixture-results.json").read_text(encoding="utf-8"))
    assert _normalise(fresh) == _normalise(committed)


def test_cli_run_with_ground_truth(tmp_path, capsys):
    out = tmp_path / "out"
    server = " ".join(f'"{p}"' for p in server_cmd())
    code = main(
        [
            "run",
            "--suite",
            DOJO_SUITE,
            "--mapping",
            str(FIXTURES / "fixture-mapping-notes.yaml"),
            "--stdio",
            server,
            "--model",
            "ground-truth",
            "--attack",
            "direct",
            "--out-dir",
            str(out),
        ]
    )
    assert code == 0, capsys.readouterr().err
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert [t["utility"] for t in data["user_tasks"]] == [True, True, True]
    assert all(p["attack_success"] is False for p in data["pairs"])
    assert (
        (out / "report.md").read_text(encoding="utf-8").startswith("# agentdojo-mcp report: notes")
    )
    seeds = [c for c in data["calls"] if c["suite_tool"] == "(seed)"]
    assert seeds and all("suite environment" in c["arguments"]["state"] for c in seeds)
    # The injected environment reached the server: the injected read returns more bytes.
    reads = [c for c in data["calls"] if c["suite_tool"] == "read_note"]
    plain = {c["result_bytes"] for c in reads if not c["injection_task"]}
    attacked = {c["result_bytes"] for c in reads if c["injection_task"]}
    assert max(plain) < min(attacked)


def test_suites_profile_lists_the_builtin_suites(capsys):
    assert main(["suites", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    names = {row["suite"] for row in doc["suites"]}
    assert {"banking", "slack", "travel", "workspace"} <= names


def test_run_rejects_an_attack_that_needs_a_known_model_name(tmp_path, capsys):
    server = " ".join(f'"{p}"' for p in server_cmd())
    code = main(
        [
            "run",
            "--suite",
            DOJO_SUITE,
            "--mapping",
            str(FIXTURES / "fixture-mapping-notes.yaml"),
            "--stdio",
            server,
            "--model",
            "ground-truth",
            "--attack",
            "important_instructions",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "direct" in capsys.readouterr().err
