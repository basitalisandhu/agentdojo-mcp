from __future__ import annotations

import pytest

from agentdojo_mcp import yamlish


def test_plain_scalars():
    assert yamlish.load("a: 1\nb: 2.5\nc: true\nd: false\ne: null\nf: ~\ng: text here\n") == {
        "a": 1,
        "b": 2.5,
        "c": True,
        "d": False,
        "e": None,
        "f": None,
        "g": "text here",
    }


def test_nested_mappings_and_empty_value():
    doc = "tools:\n  read:\n    mode: server\n    arguments:\n      a: b\n  other:\n"
    assert yamlish.load(doc) == {
        "tools": {"read": {"mode": "server", "arguments": {"a": "b"}}, "other": None}
    }


def test_comments_are_ignored_but_hash_inside_quotes_is_kept():
    doc = "# header\nkey: \"a # b\"  # trailing\nother: 'c # d'\n  # indented comment\n"
    assert yamlish.load(doc) == {"key": "a # b", "other": "c # d"}


def test_flow_json_values():
    assert yamlish.load('a: {"x": [1, 2]}\nb: []\nc: {}\n') == {
        "a": {"x": [1, 2]},
        "b": [],
        "c": {},
    }


def test_block_lists_of_scalars_and_mappings():
    doc = "items:\n  - one\n  - 2\n  - name: x\n    value: y\n  -\n    k: v\n"
    assert yamlish.load(doc) == {"items": ["one", 2, {"name": "x", "value": "y"}, {"k": "v"}]}


def test_quoted_keys_and_single_quote_escape():
    assert yamlish.load("\"a b\": 'it''s'\n") == {"a b": "it's"}


def test_json_document_is_accepted():
    assert yamlish.load('{"version": 1, "tools": {}}') == {"version": 1, "tools": {}}


def test_empty_document_is_none():
    assert yamlish.load("# only a comment\n\n") is None


@pytest.mark.parametrize(
    "doc, message",
    [
        ("a: 1\na: 2\n", "duplicate key"),
        ("a:\n\tb: 1\n", "tabs"),
        ("a: 1\n    b: 2\n", "indentation"),
        ("a: &anchor x\n", "unsupported"),
        ("a: [1, 2\n", "JSON flow"),
        ("just text without colon\n", "key: value"),
    ],
)
def test_errors_name_the_problem(doc, message):
    with pytest.raises(yamlish.YamlError, match=message):
        yamlish.load(doc)


@pytest.mark.parametrize(
    "value, text",
    [
        ("plain", "plain"),
        ("true", '"true"'),
        ("12", '"12"'),
        ("", '""'),
        ("has: colon", '"has: colon"'),
        (None, "null"),
        (3, "3"),
        ([], "[]"),
        ({"a": None}, '{"a": null}'),
    ],
)
def test_scalar_quotes_only_when_needed(value, text):
    assert yamlish.scalar(value) == text


def test_dump_then_load_round_trips():
    data = {
        "version": 1,
        "name": "x y",
        "flags": ["a", "true", 3],
        "nested": {"k": {"deep": "v"}, "empty": {}},
        "list": [{"a": 1, "b": "2"}],
    }
    assert yamlish.load(yamlish.dump(data)) == data
