from __future__ import annotations

import json
import subprocess
import sys

import pytest

from agentdojo_mcp.cli import main

from .conftest import FIXTURES, ROOT, server_cmd

SERVER_CMD = " ".join(f'"{p}"' for p in server_cmd())
NOTES_SUITE = str(FIXTURES / "fixture-suite-notes.json")
NOTES_SERVER = str(FIXTURES / "fixture-server-notes.json")
NOTES_MAPPING = str(FIXTURES / "fixture-mapping-notes.yaml")


def test_help_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "Run AgentDojo against MCP servers" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_no_command_prints_help_and_exits_2(capsys):
    assert main([]) == 2
    assert "usage: agentdojo-mcp" in capsys.readouterr().out


def test_module_entry_point():
    out = subprocess.run(
        [sys.executable, "-m", "agentdojo_mcp", "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert out.returncode == 0 and "usage: agentdojo-mcp" in out.stdout


def test_inspect_text(capsys):
    assert main(["inspect", "--stdio", SERVER_CMD]) == 0
    out = capsys.readouterr().out
    assert "fixture-notes 0.1.0" in out and "notes_create(name: string, text: string)" in out


def test_inspect_json_matches_the_committed_server_dump(capsys, tmp_path):
    out_file = tmp_path / "tools.json"
    assert main(["inspect", "--stdio", SERVER_CMD, "--json", "-o", str(out_file)]) == 0
    printed = json.loads(capsys.readouterr().out)
    committed = json.loads((FIXTURES / "fixture-server-notes.json").read_text(encoding="utf-8"))
    assert printed["tools"] == committed["tools"]
    assert json.loads(out_file.read_text(encoding="utf-8"))["tools"] == committed["tools"]


def test_inspect_unreachable_server_exits_2(capsys):
    assert main(["inspect", "--stdio", "/nonexistent/agentdojo-mcp-test-binary"]) == 2
    assert "cannot start" in capsys.readouterr().err


def test_map_to_stdout_and_file(capsys, tmp_path):
    assert main(["map", "--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER]) == 0
    captured = capsys.readouterr()
    assert "server_tool: notes_get" in captured.out
    assert "proposed 3 of 4 suite tools" in captured.err
    target = tmp_path / "m.yaml"
    assert (
        main(["map", "--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER, "-o", str(target)]) == 0
    )
    assert (
        main(["map", "--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER, "-o", str(target)]) == 2
    )
    assert "--force" in capsys.readouterr().err
    assert (
        main(
            [
                "map",
                "--suite",
                NOTES_SUITE,
                "--server-dump",
                NOTES_SERVER,
                "-o",
                str(target),
                "--force",
            ]
        )
        == 0
    )


def test_validate_exit_codes(capsys, tmp_path):
    args = ["--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER]
    assert main(["validate-mapping", NOTES_MAPPING, *args]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        (FIXTURES / "fixture-mapping-notes.yaml")
        .read_text(encoding="utf-8")
        .replace("notes_get", "notes_fetch"),
        encoding="utf-8",
    )
    assert main(["validate-mapping", str(bad), *args]) == 1
    assert "did you mean notes_get" in capsys.readouterr().out


def test_validate_strict_turns_warnings_into_failures(capsys, tmp_path):
    args = ["--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER]
    noseed = tmp_path / "noseed.yaml"
    text = (FIXTURES / "fixture-mapping-notes.yaml").read_text(encoding="utf-8")
    noseed.write_text(
        text.replace("seed:\n  tool: load_state\n  argument: state\n", ""), encoding="utf-8"
    )
    assert main(["validate-mapping", str(noseed), *args]) == 0
    assert main(["validate-mapping", str(noseed), *args, "--strict"]) == 1
    capsys.readouterr()
    assert main(["validate-mapping", str(noseed), *args, "--json", "--strict"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["warnings"] == 1 and doc["findings"][0]["where"] == "seed"


def test_validate_json_output(capsys):
    assert (
        main(
            [
                "validate-mapping",
                NOTES_MAPPING,
                "--suite",
                NOTES_SUITE,
                "--server-dump",
                NOTES_SERVER,
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc == {"ok": True, "errors": 0, "warnings": 0, "findings": []}


def test_validate_bad_mapping_file_exits_2(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 7\n", encoding="utf-8")
    assert (
        main(["validate-mapping", str(bad), "--suite", NOTES_SUITE, "--server-dump", NOTES_SERVER])
        == 2
    )
    assert "version" in capsys.readouterr().err


def test_replay_formats(capsys, tmp_path):
    results = str(FIXTURES / "fixture-results.json")
    assert main(["replay", results]) == 0
    assert "Targeted attack success" in capsys.readouterr().out
    out = tmp_path / "report.md"
    assert main(["replay", results, "--format", "markdown", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# agentdojo-mcp report")
    assert main(["replay", results, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["suite"] == "notes"


def test_replay_missing_file_exits_2(capsys, tmp_path):
    assert main(["replay", str(tmp_path / "nope.json")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_commands_needing_agentdojo_fail_cleanly_without_it(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "agentdojo", None)  # makes "import agentdojo" raise
    assert main(["suites"]) == 2
    assert 'pip install "agentdojo-mcp[dojo]"' in capsys.readouterr().err
    assert main(["map", "--suite", "banking", "--server-dump", NOTES_SERVER]) == 2
    assert "needs AgentDojo" in capsys.readouterr().err
    code = main(
        [
            "run",
            "--suite",
            "banking",
            "--mapping",
            NOTES_MAPPING,
            "--stdio",
            SERVER_CMD,
            "--model",
            "ground-truth",
        ]
    )
    assert code == 2
    assert "needs AgentDojo" in capsys.readouterr().err


def test_argparse_usage_errors_exit_2():
    with pytest.raises(SystemExit) as exc:
        main(["inspect"])
    assert exc.value.code == 2
