from __future__ import annotations

import pytest

from agentdojo_mcp.schema import (
    DumpError,
    load_server_dump,
    load_suite_dump,
    schema_types,
    server_surface,
    suite_surface,
    types_compatible,
)

from .conftest import FIXTURES


def test_schema_types_handles_type_lists_anyof_and_enums():
    assert schema_types({"type": "string"}) == {"string"}
    assert schema_types({"type": ["string", "null"]}) == {"string", "null"}
    assert schema_types({"anyOf": [{"type": "integer"}, {"type": "null"}]}) == {"integer", "null"}
    assert schema_types({"enum": ["a", 1]}) == {"string", "integer"}
    assert schema_types({"$ref": "#/$defs/X"}) == frozenset()


@pytest.mark.parametrize(
    "a, b, ok",
    [
        ({"string"}, {"string"}, True),
        ({"integer"}, {"number"}, True),
        ({"number"}, {"integer"}, True),
        ({"string"}, {"integer"}, False),
        ({"string", "null"}, {"string"}, True),
        (set(), {"object"}, True),
    ],
)
def test_types_compatible(a, b, ok):
    assert types_compatible(frozenset(a), frozenset(b)) is ok


def test_server_dump_from_inspect_output():
    surface = load_server_dump(FIXTURES / "fixture-server-notes.json")
    assert set(surface.tools) == {"notes_list", "notes_get", "notes_create", "load_state"}
    get = surface.tools["notes_get"]
    assert get.required == ["name"] and get.params["name"].types == {"string"}
    assert surface.meta["name"] == "fixture-notes"


def test_server_surface_accepts_a_plain_tool_list_and_marks_open_schemas():
    surface = server_surface([{"name": "any", "inputSchema": {"type": "object"}}])
    assert surface.tools["any"].open_params is True


@pytest.mark.parametrize(
    "data, message",
    [
        ({"nothing": 1}, "tools"),
        ([{"description": "no name"}], "no name"),
        ([{"name": "a"}, {"name": "a"}], "duplicate"),
        ([{"name": "a", "inputSchema": []}], "not an object"),
    ],
)
def test_server_surface_errors(data, message):
    with pytest.raises(DumpError, match=message):
        server_surface(data)


def test_suite_dump_keeps_mutation_flags():
    surface = load_suite_dump(FIXTURES / "fixture-suite-ledger.json")
    assert surface.tools["send_money"].mutates is True
    assert surface.tools["get_balance"].mutates is False
    assert surface.tools["get_iban"].mutates is None
    assert surface.meta["suite"] == "ledger"


def test_suite_dump_must_say_its_format():
    with pytest.raises(DumpError, match="not a suite dump"):
        suite_surface({"tools": []})


def test_unreadable_dump_is_a_dump_error(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(DumpError, match="not valid JSON"):
        load_server_dump(bad)
    with pytest.raises(DumpError, match="cannot read"):
        load_server_dump(tmp_path / "missing.json")
