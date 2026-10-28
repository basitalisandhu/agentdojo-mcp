"""Seeded fuzz, bad-input and scaling tests for every reader: the YAML subset, mapping files,
server and suite dumps, results files, and what an MCP server sends back.

Each test runs under a wall-clock limit, so a reader that loops or backtracks fails instead of
hanging CI. None needs AgentDojo, a model or a network.
"""

from __future__ import annotations

import json
import random
import signal
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

from agentdojo_mcp import mapping, report, results, schema, yamlish
from agentdojo_mcp.cli import main
from agentdojo_mcp.mcp_client import McpConnectionError, StdioTransport, result_text

from .conftest import FIXTURES

SEED = 20261028


@contextmanager
def time_limit(seconds: float):
    """Fail if the block runs longer than `seconds` (POSIX; elsewhere a plain block)."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise(signum, frame):
        raise TimeoutError(f"took longer than {seconds}s")

    old = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


SCALARS: list[Any] = [
    None,
    True,
    False,
    0,
    -1,
    2.5,
    "",
    "x",
    "server",
    "local",
    "both",
    "auto",
    "text",
    "json",
    "string",
    "object",
    [],
    {},
    "agentdojo-mcp",
    1,
]
KEYS = [
    "version",
    "suite",
    "tools",
    "server_tool",
    "mode",
    "arguments",
    "constants",
    "drop",
    "result",
    "seed",
    "tool",
    "argument",
    "name",
    "inputSchema",
    "properties",
    "type",
    "required",
    "description",
    "format",
    "user_tasks",
    "pairs",
    "calls",
    "injection_tasks",
    "suite_tool",
    "result_bytes",
    "is_error",
    "mapping",
    "utility",
    "attack_success",
    "user_task",
    "injection_task",
    "server",
    "enum",
    "items",
    "anyOf",
    "mutates",
    "id",
    "notes",
    "content",
    "seq",
]


def _value(rng: random.Random, depth: int = 0) -> Any:
    roll = rng.random()
    if depth > 4 or roll < 0.4:
        return rng.choice(SCALARS)
    if roll < 0.75:
        return {rng.choice(KEYS): _value(rng, depth + 1) for _ in range(rng.randint(0, 5))}
    return [_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def test_fuzz_yaml_subset_raises_only_yaml_error():
    rng = random.Random(SEED)
    pieces = [
        "- ",
        "-",
        "a: ",
        "b:",
        "  ",
        "    ",
        "\n",
        "'",
        '"',
        "{",
        "[",
        "]",
        "}",
        "#",
        " x",
        "1",
        "- - -:",
        "\t",
        ":",
        "null",
        "&a",
        "~",
        "\\",
        '"k": ',
        "---\n",
    ]
    with time_limit(60):
        for _ in range(5000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 50)))
            with suppress(yamlish.YamlError):
                yamlish.load(text)


def test_yaml_round_trip_of_random_documents():
    rng = random.Random(SEED)
    with time_limit(60):
        for _ in range(1000):
            data = _value(rng)
            if not isinstance(data, (dict, list)) or not data:
                continue
            assert yamlish.load(yamlish.dump(data)) == json.loads(json.dumps(data))


@pytest.mark.parametrize(
    "text",
    [
        "\n".join(" " * i + "-" for i in range(3000)),
        "\n".join("  " * i + f"k{i}:" for i in range(3000)),
        "a: " + "[" * 100_000,
        "{" * 100_000,
    ],
)
def test_deep_yaml_is_an_error_not_a_crash(text: str):
    with time_limit(30), pytest.raises(yamlish.YamlError):
        yamlish.load(text)


def test_fuzz_structural_readers_raise_only_their_errors():
    rng = random.Random(SEED)
    with time_limit(60):
        for _ in range(3000):
            data = _value(rng)
            with suppress(mapping.MappingError):
                mapping.from_data(data)
            with suppress(mapping.MappingError):
                mapping.from_data({"version": 1, "suite": "s", "tools": {"t": _value(rng)}})
            with suppress(schema.DumpError):
                schema.server_surface(data)
            with suppress(schema.DumpError):
                schema.server_surface({"tools": [_value(rng), _value(rng)]})
            with suppress(schema.DumpError):
                schema.suite_surface({"format": schema.SUITE_DUMP_FORMAT, "tools": [_value(rng)]})
            result_text(data if isinstance(data, dict) else {"content": data})


def test_fuzz_results_are_refused_or_rendered():
    rng = random.Random(SEED)
    with time_limit(60):
        for _ in range(3000):
            data = _value(rng)
            doc = {"tool": "agentdojo-mcp", "format": 1, **(data if isinstance(data, dict) else {})}
            try:
                results.check(doc)
            except results.ResultsError:
                continue
            for fmt in report.FORMATS:
                report.render(doc, fmt)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("argv", "name", "data", "needle"),
    [
        (
            ["replay"],
            "r.json",
            b'{"tool": "caf\xe9"}',
            "is not UTF-8 text (byte 0xe9 at offset 13)",
        ),
        (["replay"], "r.json", b"[" * 100_000, "nested too deeply"),
        (
            ["replay"],
            "r.json",
            json.dumps(
                {
                    "tool": "agentdojo-mcp",
                    "format": 1,
                    "calls": [{"suite_tool": "t", "result_bytes": "12"}],
                }
            ).encode(),
            "calls[0].result_bytes must be an integer",
        ),
        (
            ["replay"],
            "r.json",
            json.dumps(
                {"tool": "agentdojo-mcp", "format": 1, "user_tasks": [{"utility": True}]}
            ).encode(),
            "user_tasks[0] needs a string id",
        ),
        (
            ["replay"],
            "r.json",
            json.dumps({"tool": "agentdojo-mcp", "format": 1, "mapping": []}).encode(),
            "mapping must be an object",
        ),
    ],
)
def test_bad_results_files_exit_2_naming_the_problem(tmp_path, capsys, argv, name, data, needle):
    path = _write(tmp_path, name, data)
    with time_limit(30):
        rc = main([*argv, str(path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert needle in err and "Traceback" not in err


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        (b"version: 1\nsuite: caf\xe9\n", "not UTF-8 text (byte 0xe9 at offset 21)"),
        (
            b"version: 1\nsuite: s\ntools:\n  t:\n    mode: bogus\n    server_tool: x\n",
            "tools.t.mode: expected one of",
        ),
        (b"version: 1\nsuite: s\ntools:\n  t: " + b"[" * 100_000 + b"\n", "nested too deeply"),
    ],
)
def test_bad_mapping_files_exit_2_naming_the_file(tmp_path, capsys, data, needle):
    path = _write(tmp_path, "m.yaml", data)
    with time_limit(30):
        rc = main(
            [
                "validate-mapping",
                str(path),
                "--suite",
                str(FIXTURES / "fixture-suite-notes.json"),
                "--server-dump",
                str(FIXTURES / "fixture-server-notes.json"),
            ]
        )
    err = capsys.readouterr().err
    assert rc == 2
    assert f"{path}: " in err and needle in err


def test_bad_server_dump_exits_2(tmp_path, capsys):
    path = _write(tmp_path, "s.json", b"[" * 100_000)
    rc = main(
        [
            "validate-mapping",
            str(FIXTURES / "fixture-mapping-notes.yaml"),
            "--suite",
            str(FIXTURES / "fixture-suite-notes.json"),
            "--server-dump",
            str(path),
        ]
    )
    assert rc == 2
    assert "nested too deeply" in capsys.readouterr().err


def test_unwritable_output_exits_2(tmp_path, capsys):
    (tmp_path / "dir").mkdir()
    rc = main(
        [
            "replay",
            str(FIXTURES / "fixture-results.json"),
            "--format",
            "markdown",
            "-o",
            str(tmp_path / "dir"),
        ]
    )
    assert rc == 2
    assert f"cannot write {tmp_path / 'dir'}" in capsys.readouterr().err


def test_stdio_command_with_an_unclosed_quote():
    with pytest.raises(McpConnectionError, match="cannot split into words"):
        StdioTransport("python -c 'print(1)")


SERVER_WITH_BAD_BYTES = r"""
import sys
line = sys.stdin.readline()
out = sys.stdout.buffer
out.write(b"\xff\xfe not utf-8 \x80\n")
out.write(b"[" * 100000 + b"\n")
out.write(b'{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n')
out.flush()
sys.stdin.readline()
"""


def test_stdio_reader_survives_bad_bytes_and_deep_json(tmp_path):
    """Non-UTF-8 bytes used to kill the reader thread, so the request waited for its timeout."""
    script = tmp_path / "server.py"
    script.write_text(SERVER_WITH_BAD_BYTES, encoding="utf-8")
    transport = StdioTransport([sys.executable, str(script)])
    try:
        with time_limit(20):
            start = time.monotonic()
            assert transport.request("ping", None, timeout=15) == {"ok": True}
            assert time.monotonic() - start < 10
    finally:
        transport.close()


def _surface(rng: random.Random, n: int, params: int, prefix: str) -> list[dict[str, Any]]:
    verbs = ["get", "list", "send", "create", "update", "delete", "search", "read"]
    nouns = ["balance", "transaction", "file", "email", "event", "user", "channel", "note", "id"]
    tools = []
    for i in range(n):
        name = f"{rng.choice(verbs)}_{rng.choice(nouns)}_{prefix}{i}"
        props = {
            f"{rng.choice(nouns)}_{rng.choice(['id', 'name', 'text'])}": {
                "type": rng.choice(["string", "integer", "boolean"])
            }
            for _ in range(params)
        }
        tools.append(
            {
                "name": name,
                "description": f"{name.replace('_', ' ')}.",
                "inputSchema": {"type": "object", "properties": props, "required": list(props)[:1]},
            }
        )
    return tools


def test_propose_scales_linearly_with_server_tools():
    """propose compares every suite tool with every server tool, so 10x the server tools is
    about 10x the time; the cached name comparison keeps the constant small."""
    rng = random.Random(SEED)
    suite = schema.suite_surface(
        {"format": schema.SUITE_DUMP_FORMAT, "suite": "s", "tools": _surface(rng, 20, 4, "s")}
    )

    def timed(n: int) -> float:
        mapping.name_similarity.cache_clear()
        server = schema.server_surface({"tools": _surface(rng, n, 6, "t")})
        start = time.perf_counter()
        mapping.propose(suite, server, "s", None)
        return time.perf_counter() - start

    with time_limit(120):
        small, large = timed(20), timed(200)
    assert large < max(small, 0.01) * 25


def test_yaml_and_report_scale_linearly():
    def timed(n: int) -> float:
        tools = {
            f"tool_{i}": {
                "server_tool": f"srv_{i}",
                "mode": "server",
                "arguments": {"a": "b"},
                "drop": ["q"],
            }
            for i in range(n)
        }
        text = yamlish.dump({"version": 1, "suite": "banking", "tools": tools})
        doc = {
            "tool": "agentdojo-mcp",
            "format": 1,
            "calls": [
                {
                    "seq": k,
                    "suite_tool": f"tool_{k % 40}",
                    "arguments": {"a": k},
                    "result_bytes": 10,
                }
                for k in range(n * 4)
            ],
        }
        start = time.perf_counter()
        mapping.from_data(yamlish.load(text))
        results.check(doc)
        report.render(doc, "markdown")
        return time.perf_counter() - start

    with time_limit(60):
        small, large = timed(500), timed(5_000)
    assert large < max(small, 0.005) * 40
