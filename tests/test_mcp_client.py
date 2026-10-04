from __future__ import annotations

import sys

import pytest

from agentdojo_mcp.mcp_client import (
    HttpTransport,
    McpConnectionError,
    McpError,
    child_environment,
    connect,
    parse_headers,
    result_text,
)

from .conftest import server_cmd


def test_stdio_handshake_and_tools():
    with connect(stdio=server_cmd()) as client:
        assert client.server.name == "fixture-notes"
        assert client.server.protocol_version == "2025-06-18"
        assert client.server.transport == "stdio"
        names = [t["name"] for t in client.list_tools()]
    assert names == ["notes_list", "notes_get", "notes_create", "load_state"]


def test_stdio_command_string_is_split_like_a_shell():
    cmd = " ".join(f'"{part}"' for part in server_cmd())
    with connect(stdio=cmd) as client:
        assert client.server.name == "fixture-notes"


def test_pagination_follows_next_cursor():
    with connect(stdio=server_cmd("--paginate")) as client:
        assert len(client.list_tools()) == 4


def test_non_json_stdout_lines_are_skipped():
    with connect(stdio=server_cmd("--noise")) as client:
        assert client.list_tools()


def test_server_requests_are_declined_and_the_session_continues():
    with connect(stdio=server_cmd("--server-request")) as client:
        assert client.server.name == "fixture-notes"


def test_call_tool_and_error_results():
    with connect(stdio=server_cmd()) as client:
        ok = client.call_tool("notes_get", {"name": "groceries"})
        assert result_text(ok) == "milk, eggs" and not ok.get("isError")
        missing = client.call_tool("notes_get", {"name": "nope"})
        assert missing["isError"] is True
        with pytest.raises(McpError, match="unknown tool"):
            client.call_tool("no_such_tool", {})


def test_server_that_exits_reports_its_stderr():
    with pytest.raises(McpConnectionError, match="exiting on purpose"):
        connect(stdio=server_cmd("--exit-on-init"))


def test_missing_executable_is_a_connection_error():
    with pytest.raises(McpConnectionError, match="cannot start"):
        connect(stdio=["/nonexistent/agentdojo-mcp-test-binary"])


def test_timeout_is_reported():
    with (
        connect(stdio=server_cmd("--slow"), timeout=0.5) as client,
        pytest.raises(McpError, match="no response within"),
    ):
        client.call_tool("notes_list", {})


def test_exactly_one_target():
    with pytest.raises(McpError, match="exactly one"):
        connect()
    with pytest.raises(McpError, match="exactly one"):
        connect(stdio="x", url="https://example.invalid/mcp")


def test_child_environment_passes_only_basics_and_requested_keys(monkeypatch):
    monkeypatch.setenv("AGENTDOJO_MCP_TEST_PROVIDER_KEY", "not-for-the-server")
    monkeypatch.setenv("AGENTDOJO_MCP_TEST_WANTED", "yes")
    env = child_environment(["AGENTDOJO_MCP_TEST_WANTED", "EXTRA=1"])
    assert "AGENTDOJO_MCP_TEST_PROVIDER_KEY" not in env
    assert env["AGENTDOJO_MCP_TEST_WANTED"] == "yes" and env["EXTRA"] == "1"
    with pytest.raises(McpError, match="not set"):
        child_environment(["AGENTDOJO_MCP_TEST_UNSET_VARIABLE"])


def test_stdio_server_does_not_see_unrequested_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTDOJO_MCP_TEST_PROVIDER_KEY", "not-for-the-server")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m.get('method') == 'initialize':\n"
        "        leaked = 'AGENTDOJO_MCP_TEST_PROVIDER_KEY' in os.environ\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'protocolVersion': '2025-06-18',"
        " 'serverInfo': {'name': 'leak' if leaked else 'clean', 'version': '0'}}}), flush=True)\n",
        encoding="utf-8",
    )
    with connect(stdio=[sys.executable, str(probe)]) as client:
        assert client.server.name == "clean"


def test_parse_headers(monkeypatch):
    monkeypatch.setenv("AGENTDOJO_MCP_TEST_TOKEN", "abc")
    assert parse_headers(["Authorization: Bearer x", "X-Key: ${AGENTDOJO_MCP_TEST_TOKEN}"]) == {
        "Authorization": "Bearer x",
        "X-Key": "abc",
    }
    with pytest.raises(McpError, match="Name: value"):
        parse_headers(["no-colon"])
    with pytest.raises(McpError, match="is not set"):
        parse_headers(["X: ${AGENTDOJO_MCP_TEST_UNSET_VARIABLE}"])


@pytest.mark.parametrize("http_server", ["json", "sse"], indirect=True)
def test_http_transport_json_and_sse(http_server, request):
    with connect(url=http_server) as client:
        assert client.server.transport == "http"
        assert len(client.list_tools()) == 4
        assert result_text(client.call_tool("notes_get", {"name": "groceries"})) == "milk, eggs"
    seen = request.node.http_seen
    assert seen["session"][0] == "" and seen["session"][-1] == "fixture-session"
    assert seen["version"][-1] == "2025-06-18"


def test_plain_http_is_refused_for_remote_hosts():
    with pytest.raises(McpConnectionError, match="only accepted for localhost"):
        HttpTransport("http://example.invalid/mcp")
    HttpTransport("http://example.invalid/mcp", allow_http=True)
    with pytest.raises(McpConnectionError, match="http or https"):
        HttpTransport("ftp://example.invalid/mcp")


def test_unreachable_url_is_a_connection_error():
    with pytest.raises(McpConnectionError, match="cannot reach"):
        connect(url="http://127.0.0.1:9/mcp", timeout=2)


def test_result_text_summarises_non_text_blocks():
    assert (
        result_text({"content": [{"type": "text", "text": "a"}, {"type": "image"}]})
        == "a\n[image content]"
    )
