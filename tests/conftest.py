"""Shared fixtures: paths, the stdio fixture server command, an in-process HTTP MCP server, and
the ``dojo`` marker, which skips tests when AgentDojo is not installed."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SERVER = FIXTURES / "fixture-server.py"
DOJO_SUITE = f"{FIXTURES / 'fixture-dojo-suite' / 'fixture-suite.py'}:suite"


def server_cmd(*flags: str) -> list[str]:
    return [sys.executable, str(SERVER), *flags]


def has_agentdojo() -> bool:
    return importlib.util.find_spec("agentdojo") is not None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if has_agentdojo():
        return
    skip = pytest.mark.skip(reason="AgentDojo is not installed (pip install -e '.[dojo]')")
    for item in items:
        if "dojo" in item.keywords:
            item.add_marker(skip)


def _load_fixture_server():
    spec = importlib.util.spec_from_file_location("fixture_server_module", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def http_server(request: pytest.FixtureRequest) -> Iterator[str]:
    """A Streamable HTTP endpoint backed by the fixture server's handle(); parametrise with
    indirect=["http_server"] and "sse" to answer with Server-Sent Events."""
    module = _load_fixture_server()
    use_sse = getattr(request, "param", "json") == "sse"
    seen: dict[str, list[str]] = {"session": [], "version": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["session"].append(self.headers.get("Mcp-Session-Id") or "")
            seen["version"].append(self.headers.get("MCP-Protocol-Version") or "")
            response = module.handle(body)
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            payload = json.dumps(response).encode()
            self.send_response(200)
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "fixture-session")
            if use_sse:
                payload = b"event: message\ndata: " + payload + b"\n\n"
                self.send_header("Content-Type", "text/event-stream")
            else:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_DELETE(self) -> None:
            self.send_response(200)
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    request.node.http_seen = seen
    try:
        yield url
    finally:
        httpd.shutdown()
        httpd.server_close()
