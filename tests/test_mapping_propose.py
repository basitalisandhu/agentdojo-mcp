from __future__ import annotations

import pytest

from agentdojo_mcp.mapping import (
    description_phrase,
    description_similarity,
    dumps,
    from_data,
    load,
    name_similarity,
    propose,
    score_pair,
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


def test_description_phrase_takes_the_first_sentence_without_stop_words():
    assert description_phrase("List recent transactions.") == "List_recent_transactions"
    assert description_phrase("Get the account IBAN. More text.") == "Get_account_IBAN"
    assert description_phrase("") == ""
    assert description_phrase("one two three four five six seven eight") == (
        "one_two_three_four_five_six"
    )


def test_description_similarity_rescues_a_pair_names_miss(ledger):
    suite, server = ledger
    recent = suite.tools["get_most_recent_transactions"]
    listing = server.tools["list_transactions"]
    assert name_similarity(recent.name, listing.name) < 0.45
    assert description_similarity(recent, listing) >= 0.6
    assert score_pair(recent, listing)[0] >= 0.45


def test_ledger_proposes_list_transactions_from_the_descriptions(ledger):
    suite, server = ledger
    tools = propose(suite, server, "ledger", None).tools
    assert tools["get_most_recent_transactions"].server_tool == "list_transactions"
    # A single shared description word is not enough evidence.
    assert tools["get_iban"].server_tool is None and tools["get_iban"].mode == "local"


def test_weak_description_overlap_leaves_the_score_unchanged(ledger):
    suite, server = ledger
    iban, balance = suite.tools["get_iban"], server.tools["account_balance"]
    assert description_similarity(iban, balance) < 0.6
    name_only = 0.65 * name_similarity(iban.name, balance.name)
    assert score_pair(iban, balance)[0] < 0.45 and score_pair(iban, balance)[0] >= name_only
