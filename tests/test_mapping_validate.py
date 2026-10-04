from __future__ import annotations

import copy

import pytest

from agentdojo_mcp.mapping import MappingError, from_data, load, to_data, validate
from agentdojo_mcp.schema import load_server_dump, load_suite_dump

from .conftest import FIXTURES

SUITE = load_suite_dump(FIXTURES / "fixture-suite-notes.json")
SERVER = load_server_dump(FIXTURES / "fixture-server-notes.json")
GOOD = to_data(load(FIXTURES / "fixture-mapping-notes.yaml"))


def findings_for(change) -> dict[str, str]:
    data = copy.deepcopy(GOOD)
    change(data)
    return {f.where: f.level for f in validate(from_data(data), SUITE, SERVER)}


def test_the_committed_mapping_is_clean():
    assert validate(from_data(GOOD), SUITE, SERVER) == []


def test_unknown_suite_tool_is_an_error_with_a_hint():
    data = copy.deepcopy(GOOD)
    data["tools"]["read_notes"] = data["tools"].pop("read_note")
    findings = validate(from_data(data), SUITE, SERVER)
    err = next(f for f in findings if f.where == "tools.read_notes")
    assert err.level == "error" and "did you mean read_note" in err.message
    assert any(f.where == "tools.read_note" and f.level == "warning" for f in findings)


def test_unknown_server_tool_is_an_error():
    found = findings_for(lambda d: d["tools"]["read_note"].update(server_tool="notes_fetch"))
    assert found["tools.read_note.server_tool"] == "error"


def test_unmapped_parameter_that_the_server_lacks():
    found = findings_for(lambda d: d["tools"]["read_note"].update(arguments={}))
    assert found["tools.read_note.arguments.title"] == "error"
    assert found["tools.read_note"] == "error"  # notes_get requires name


def test_mapping_a_parameter_the_suite_tool_does_not_have():
    found = findings_for(lambda d: d["tools"]["read_note"]["arguments"].update(path="name"))
    assert found["tools.read_note.arguments.path"] == "error"


def test_two_suite_parameters_feeding_one_server_parameter():
    found = findings_for(
        lambda d: d["tools"]["add_note"].update(arguments={"title": "name", "body": "name"})
    )
    assert found["tools.add_note.arguments.body"] == "error"


def test_parameter_both_mapped_and_dropped():
    found = findings_for(lambda d: d["tools"]["read_note"].update(drop=["title"]))
    assert found["tools.read_note.arguments.title"] == "error"


def test_constant_for_a_parameter_the_server_lacks_and_null_required_constant():
    found = findings_for(lambda d: d["tools"]["add_note"].update(constants={"colour": "red"}))
    assert found["tools.add_note.constants.colour"] == "error"
    found = findings_for(
        lambda d: d["tools"]["add_note"].update(
            arguments={"title": "name"}, drop=["body"], constants={"text": None}
        )
    )
    assert found["tools.add_note.constants.text"] == "error"


def test_constant_type_mismatch_is_a_warning():
    found = findings_for(
        lambda d: d["tools"]["add_note"].update(
            arguments={"title": "name"}, drop=["body"], constants={"text": 5}
        )
    )
    assert found["tools.add_note.constants.text"] == "warning"


def test_mutating_tool_in_server_mode_warns():
    found = findings_for(lambda d: d["tools"]["add_note"].update(mode="server"))
    assert found["tools.add_note.mode"] == "warning"


def test_seed_problems():
    found = findings_for(lambda d: d.update(seed={"tool": "reset", "argument": "state"}))
    assert found["seed.tool"] == "error"
    found = findings_for(lambda d: d.update(seed={"tool": "load_state", "argument": "env"}))
    assert found["seed.argument"] == "error"
    found = findings_for(lambda d: d.pop("seed"))
    assert found["seed"] == "warning"
    found = findings_for(lambda d: d["tools"]["read_note"].update(server_tool="load_state"))
    assert found["tools.read_note.server_tool"] == "error"


def test_suite_name_must_match_the_dump():
    found = findings_for(lambda d: d.update(suite="banking"))
    assert found["suite"] == "error"


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.pop("suite"), "suite"),
        (lambda d: d.update(tools={}), "tools"),
        (lambda d: d["tools"]["read_note"].update(mode="remote"), "mode"),
        (
            lambda d: d["tools"]["read_note"].update(server_tool=None, mode="server"),
            "needs a server_tool",
        ),
        (lambda d: d["tools"]["read_note"].update(extra=1), "unknown key"),
        (lambda d: d["tools"]["read_note"].update(result="xml"), "result"),
        (lambda d: d["tools"]["read_note"].update(arguments={"title": 3}), "arguments"),
        (lambda d: d["tools"]["read_note"].update(drop="title"), "drop"),
        (lambda d: d.update(seed={"argument": "x"}), "seed"),
    ],
)
def test_structural_errors(change, message):
    data = copy.deepcopy(GOOD)
    change(data)
    with pytest.raises(MappingError, match=message):
        from_data(data)


def test_translate_renames_drops_and_adds_constants():
    tm = from_data(
        {
            "version": 1,
            "suite": "s",
            "tools": {
                "t": {
                    "server_tool": "u",
                    "arguments": {"a": "x"},
                    "drop": ["b"],
                    "constants": {"c": 1},
                }
            },
        }
    ).tools["t"]
    assert tm.mode == "server"
    assert tm.translate({"a": 1, "b": 2, "d": 3}) == {"x": 1, "d": 3, "c": 1}
