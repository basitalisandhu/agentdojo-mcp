from __future__ import annotations

import pytest

from agentdojo_mcp.mapping import (
    dumps,
    from_data,
    load,
    name_similarity,
    propose,
    to_data,
    tokens,
    validate,
)
from agentdojo_mcp.schema import load_server_dump, load_suite_dump

from .conftest import FIXTURES


@pytest.fixture
def notes():
    return (
        load_suite_dump(FIXTURES / "fixture-suite-notes.json"),
        load_server_dump(FIXTURES / "fixture-server-notes.json"),
    )


@pytest.fixture
def ledger():
    return (
        load_suite_dump(FIXTURES / "fixture-suite-ledger.json"),
        load_server_dump(FIXTURES / "fixture-server-ledger.json"),
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        ("get_unread_emails", ["get", "unread", "email"]),
        ("transferFunds", ["send", "money"]),
        ("notes.list", ["note", "list"]),
        ("HTTPRequest", ["http", "request"]),
        ("read-file", ["get", "file"]),
    ],
)
def test_tokens_split_and_fold(name, expected):
    assert tokens(name) == expected


def test_name_similarity_ignores_word_order_and_plurals():
    assert name_similarity("list_notes", "notes_list") == 1.0
    assert name_similarity("read_note", "notes_get") > name_similarity("read_note", "notes_create")


def test_reading_and_writing_verbs_conflict():
    assert name_similarity("get_contact", "delete_contact") < name_similarity(
        "get_contact", "contact"
    )


def test_proposal_for_the_notes_fixture_matches_the_committed_mapping(notes):
    suite, server = notes
    proposed = propose(suite, server, "notes", "v1.2.2")
    committed = load(FIXTURES / "fixture-mapping-notes.yaml")
    assert to_data(proposed) == to_data(committed)


def test_mutating_tools_are_proposed_in_both_mode(notes):
    suite, server = notes
    tm = propose(suite, server, "notes", None).tools["add_note"]
    assert (tm.server_tool, tm.mode) == ("notes_create", "both")
    assert tm.arguments == {"title": "name", "body": "text"}


def test_tools_below_the_threshold_stay_local(notes):
    suite, server = notes
    tm = propose(suite, server, "notes", None).tools["search_notes"]
    assert tm.server_tool is None and tm.mode == "local"
    assert "runs locally" in tm.note


def test_seed_tool_is_detected_and_never_proposed_as_a_stand_in(ledger):
    suite, server = ledger
    mapping = propose(suite, server, "ledger", None)
    assert mapping.seed is not None and mapping.seed.tool == "seed"
    assert all(tm.server_tool != "seed" for tm in mapping.tools.values())


def test_ledger_proposals(ledger):
    suite, server = ledger
    tools = propose(suite, server, "ledger", "v1.2.2").tools
    assert tools["get_balance"].server_tool == "account_balance"
    assert tools["read_file"].arguments == {"file_path": "path"}
    assert tools["search_contacts"].server_tool == "find_contact"
    assert tools["search_contacts"].arguments == {"query": "q"}
    assert tools["update_password"].mode == "local"


def test_unfilled_required_server_parameters_become_null_constants(ledger):
    suite, server = ledger
    tm = propose(suite, server, "ledger", None).tools["send_money"]
    assert tm.server_tool == "transferFunds"
    assert tm.constants == {"currency": None, "to": None}
    assert "TODO" in tm.note
    findings = validate(propose(suite, server, "ledger", None), suite, server)
    assert {f.where for f in findings if f.level == "error"} == {
        "tools.send_money.constants.currency",
        "tools.send_money.constants.to",
    }


def test_written_mapping_reads_back_identically(ledger, tmp_path):
    suite, server = ledger
    mapping = propose(suite, server, "ledger", "v1.2.2")
    path = tmp_path / "m.yaml"
    path.write_text(dumps(mapping), encoding="utf-8")
    assert to_data(load(path)) == to_data(mapping)
    assert "# proposed: account_balance" in path.read_text(encoding="utf-8")


def test_mapping_without_seed_writes_a_commented_hint(notes):
    suite, server = notes
    server.tools.pop("load_state")
    text = dumps(propose(suite, server, "notes", None))
    assert "# seed:" in text and "\nseed:" not in text
    assert from_data(to_data(propose(suite, server, "notes", None))).seed is None
