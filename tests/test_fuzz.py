"""Fuzz and property tests for every input reader: mapping files (the YAML subset and JSON),
server and suite dumps, results files, MCP messages from a server, and command-line values.

The contract: bad input gives a clear error (``MappingError``, ``DumpError``, ``ResultsError``,
``McpError`` or exit code 2), never a traceback, a hang or a crash.
"""

from __future__ import annotations

import contextlib
import copy
import json
import string
import sys
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agentdojo_mcp import yamlish
from agentdojo_mcp.cli import main
from agentdojo_mcp.mapping import MappingError, from_data
from agentdojo_mcp.mapping import load as load_mapping
from agentdojo_mcp.mcp_client import McpError, child_environment, connect, parse_headers
from agentdojo_mcp.report import render
from agentdojo_mcp.results import ResultsError, check
from agentdojo_mcp.results import load as load_results
from agentdojo_mcp.schema import DumpError, server_surface, suite_surface

from .conftest import FIXTURES

FUZZ = settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**6), max_value=10**6),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=16),
)
json_values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=25,
)
YAML_ALPHABET = string.ascii_letters + string.digits + " :-#'\"{}[],\n_.\\\t"


# The YAML subset and mapping files


@FUZZ
@given(text=st.text(alphabet=YAML_ALPHABET, max_size=200))
def test_yamlish_load_only_raises_yaml_error(text):
    with contextlib.suppress(yamlish.YamlError):
        yamlish.load(text)


@FUZZ
@given(data=json_values)
def test_yamlish_round_trips_what_it_dumps(data):
    if not isinstance(data, (dict, list)) or not data:
        return
    if isinstance(data, list) and any(isinstance(x, dict) and not x for x in data):
        return
    try:
        loaded = yamlish.load(yamlish.dump(data))
    except yamlish.YamlError:
        return  # some keys (with ': ' or leading '-') are outside the subset
    if isinstance(data, dict):
        assert set(loaded) == {str(k) for k in data}


@FUZZ
@given(data=json_values)
def test_from_data_only_raises_mapping_error(data):
    with contextlib.suppress(MappingError):
        from_data(data)


@FUZZ
@given(raw=st.binary(max_size=200))
def test_load_mapping_random_bytes(tmp_path, raw):
    path = tmp_path / "m.yaml"
    path.write_bytes(raw)
    with contextlib.suppress(MappingError):
        load_mapping(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfeversion: 1\n", "not UTF-8"),
        (b'{"a":' + b"[" * 100_000 + b"]" * 100_000 + b"}", "nested too deeply"),
        ("".join(" " * (2 * i) + "-\n" for i in range(3_000)).encode(), "nested too deeply"),
    ],
)
def test_mapping_file_errors(tmp_path, content, message):
    path = tmp_path / "m.yaml"
    path.write_bytes(content)
    with pytest.raises(MappingError, match=message):
        load_mapping(path)


# Server and suite dumps


tool_like = st.fixed_dictionaries(
    {"name": st.one_of(st.text(min_size=1, max_size=8), json_values)},
    optional={"inputSchema": json_values, "description": json_values, "mutates": json_values},
)


@FUZZ
@given(data=st.one_of(json_values, st.lists(tool_like, max_size=4)))
def test_server_surface_only_raises_dump_error(data):
    for candidate in (data, {"tools": data} if isinstance(data, list) else data):
        with contextlib.suppress(DumpError):
            server_surface(candidate)


@FUZZ
@given(tools=st.lists(tool_like, max_size=4))
def test_suite_surface_only_raises_dump_error(tools):
    with contextlib.suppress(DumpError):
        suite_surface({"format": "agentdojo-mcp/suite-dump", "tools": tools})


def test_required_that_is_not_a_list():
    surface = server_surface(
        [{"name": "t", "inputSchema": {"properties": {"a": {}}, "required": 5}}]
    )
    assert surface.tools["t"].required == []


@pytest.mark.parametrize("content", [b"\xff", b"[" * 100_000 + b"]" * 100_000])
def test_server_dump_file_errors_exit_2(tmp_path, capsys, content):
    dump = tmp_path / "tools.json"
    dump.write_bytes(content)
    code = main(
        [
            "map",
            "--suite",
            str(FIXTURES / "fixture-suite-notes.json"),
            "--server-dump",
            str(dump),
        ]
    )
    assert code == 2 and "is not valid JSON" in capsys.readouterr().err


# Results files (replay)


@pytest.fixture(scope="module")
def results():
    return json.loads((FIXTURES / "fixture-results.json").read_text(encoding="utf-8"))


@FUZZ
@given(
    key=st.sampled_from(
        ["user_tasks", "injection_tasks", "pairs", "calls", "mapping", "server", "notes"]
    ),
    value=json_values,
)
def test_results_with_one_part_replaced_check_or_render(results, key, value):
    data = copy.deepcopy(results)
    data[key] = value
    try:
        check(data)
    except ResultsError:
        return
    for fmt in ("text", "markdown", "json"):
        render(data, fmt)


@FUZZ
@given(index=st.integers(min_value=0, max_value=20), field=st.text(max_size=12), value=json_values)
def test_results_with_one_call_field_replaced(results, index, field, value):
    data = copy.deepcopy(results)
    call = data["calls"][index % len(data["calls"])]
    call[field or "seq"] = value
    try:
        check(data)
    except ResultsError:
        return
    for fmt in ("text", "markdown", "json"):
        render(data, fmt)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["user_tasks"].append({"utility": True}), "user_tasks"),
        (lambda d: d["user_tasks"].append("user_task_9"), "user_tasks"),
        (lambda d: d.__setitem__("mapping", ["x"]), "mapping must be an object"),
        (lambda d: d["mapping"].__setitem__("tools", {"a": "server"}), "mapping.tools"),
        (lambda d: d["calls"][0].__setitem__("suite_tool", ["x"]), "calls"),
        (lambda d: d["pairs"][0].__setitem__("injection_task", 3), "pairs"),
    ],
)
def test_results_shape_errors(results, mutate, message):
    data = copy.deepcopy(results)
    mutate(data)
    with pytest.raises(ResultsError, match=message):
        check(data)


def test_results_with_odd_values_still_render(results):
    data = copy.deepcopy(results)
    data["calls"][0]["result_bytes"] = "lots"
    data["calls"][0]["seq"] = [1]
    data["mapping"]["tools"]["read_note"]["mode"] = ["server"]
    for fmt in ("text", "markdown", "json"):
        render(data, fmt)


@FUZZ
@given(raw=st.binary(max_size=200))
def test_load_results_random_bytes(tmp_path, raw):
    path = tmp_path / "results.json"
    path.write_bytes(raw)
    with contextlib.suppress(ResultsError):
        load_results(path)


def test_deep_results_file(tmp_path):
    path = tmp_path / "results.json"
    path.write_bytes(b"[" * 100_000 + b"]" * 100_000)
    with pytest.raises(ResultsError, match="nested too deeply"):
        load_results(path)


# MCP messages from a server


def _server(tmp_path, lines: list[bytes]) -> list[str]:
    """A stdio 'server' that writes ``lines`` after reading the initialize request and then
    answers nothing else."""
    script = tmp_path / "srv.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.buffer.readline()\n"
        f"for line in {lines!r}:\n"
        "    sys.stdout.buffer.write(line + b'\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stdin.buffer.readline()\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


GOOD_INIT = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "x", "serverInfo": {"name": "s"}}}
).encode()


def test_server_noise_before_the_response_is_skipped(tmp_path):
    noise = [b"\xff\xfe not utf-8", b"[" * 100_000 + b"]" * 100_000, b"plain text", b"42"]
    with connect(stdio=_server(tmp_path, [*noise, GOOD_INIT]), timeout=10) as client:
        assert client.server.name == "s"


def test_server_info_that_is_not_an_object(tmp_path):
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": "text"}}).encode()
    with connect(stdio=_server(tmp_path, [init]), timeout=10) as client:
        assert client.server.name == ""


def test_server_that_exits_fails_fast_not_at_the_timeout(tmp_path):
    start = time.monotonic()
    with pytest.raises(McpError, match="exited"):
        connect(stdio=[sys.executable, "-c", "print('bye')"], timeout=30)
    assert time.monotonic() - start < 15


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(result=json_values)
def test_random_initialize_results(tmp_path, result):
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
    try:
        with connect(stdio=_server(tmp_path, [init]), timeout=10):
            pass
    except McpError:
        pass


# Command-line values


@pytest.mark.parametrize("item", ["=x", "1BAD=x", "A B=x", "", "A\0=x"])
def test_bad_env_items(item):
    with pytest.raises(McpError, match="--env"):
        child_environment([item])


@pytest.mark.parametrize("item", [": v", "Bad Name: v", "X: a\nb", "noColon"])
def test_bad_header_items(item):
    with pytest.raises(McpError, match="--header"):
        parse_headers([item])


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon"])
def test_bad_timeout_exits_2(value, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["inspect", "--stdio", "true", "--timeout", value])
    assert exc.value.code == 2
    assert "seconds" in capsys.readouterr().err


def test_unclosed_quote_in_stdio_command_exits_2(capsys):
    assert main(["inspect", "--stdio", "python 'unclosed"]) == 2
    assert "No closing quotation" in capsys.readouterr().err


@FUZZ
@given(argv=st.lists(st.text(alphabet=string.ascii_letters + "-=. /", max_size=12), max_size=4))
def test_random_replay_argv_exits_cleanly(argv, capsys):
    try:
        code = main(["replay", *argv])
    except SystemExit as exc:
        code = exc.code
    capsys.readouterr()
    assert code in (0, 1, 2)
