"""A small MCP client over stdio and Streamable HTTP, written against JSON-RPC 2.0 directly.

It covers what a benchmark bridge needs: ``initialize``, ``tools/list`` (with pagination) and
``tools/call``. Server-initiated requests are declined with ``Method not found``; notifications
from the server are ignored. No MCP SDK is used, so the core of agentdojo-mcp stays standard
library only.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import __version__

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 30.0

# Environment variables a stdio server receives by default. Anything else must be passed with
# --env, so model provider keys in the caller's environment never reach the server.
if sys.platform == "win32":
    DEFAULT_ENV_KEYS = (
        "APPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "USERNAME",
        "USERPROFILE",
    )
else:
    DEFAULT_ENV_KEYS = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "TMPDIR", "LANG")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class McpError(Exception):
    """The server answered with a JSON-RPC error, or the exchange could not be completed."""


class McpConnectionError(McpError):
    """The server could not be started or reached, or it went away."""


@dataclass
class ServerInfo:
    name: str = ""
    version: str = ""
    protocol_version: str = ""
    transport: str = ""
    target: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "transport": self.transport,
            "target": self.target,
        }


def child_environment(extra: list[str] | None = None) -> dict[str, str]:
    """The default environment plus ``KEY=VALUE`` entries; a bare ``KEY`` copies it from ours."""
    env = {k: os.environ[k] for k in DEFAULT_ENV_KEYS if k in os.environ}
    for item in extra or []:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
        elif item in os.environ:
            env[item] = os.environ[item]
        else:
            raise McpError(f"--env {item}: not set in the current environment")
    return env


def parse_headers(items: list[str] | None) -> dict[str, str]:
    """Parse ``Name: value`` headers. A value of the form ``${VAR}`` is read from the environment."""
    headers: dict[str, str] = {}
    for item in items or []:
        if ":" not in item:
            raise McpError(f"--header {item!r}: expected 'Name: value'")
        name, value = item.split(":", 1)
        value = value.strip()
        if value.startswith("${") and value.endswith("}"):
            var = value[2:-1]
            if var not in os.environ:
                raise McpError(f"--header {name.strip()}: ${{{var}}} is not set")
            value = os.environ[var]
        headers[name.strip()] = value
    return headers


class _Transport:
    kind = ""
    target = ""

    def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        raise NotImplementedError

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _rpc_result(msg: dict[str, Any], method: str) -> Any:
    if "error" in msg and isinstance(msg["error"], dict):
        err = msg["error"]
        raise McpError(f"{method}: server error {err.get('code')}: {err.get('message')}")
    if "result" not in msg:
        raise McpError(f"{method}: response has neither result nor error")
    return msg["result"]


class StdioTransport(_Transport):
    kind = "stdio"

    def __init__(self, command: str | list[str], env: dict[str, str] | None = None) -> None:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            raise McpConnectionError("--stdio needs a command")
        self.target = command if isinstance(command, str) else shlex.join(argv)
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env if env is not None else child_environment(),
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise McpConnectionError(f"cannot start {argv[0]}: {exc}") from exc
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.stderr_tail: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # some servers print non-protocol text on stdout; skip it
            if isinstance(msg, dict):
                self._messages.put(msg)
        self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip("\n"))
            del self.stderr_tail[:-20]

    def _write(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            with self._lock:
                self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise McpConnectionError(f"server process is gone: {self._exit_reason()}") from exc

    def _exit_reason(self) -> str:
        code = self.proc.poll()
        tail = f"; stderr: {self.stderr_tail[-1]}" if self.stderr_tail else ""
        return (f"exited with code {code}" if code is not None else "stopped responding") + tail

    def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        rid = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"{method}: no response within {timeout:g}s")
            try:
                msg = self._messages.get(timeout=remaining)
            except queue.Empty:
                continue
            if msg is None:
                self._messages.put(None)
                raise McpConnectionError(f"{method}: server process {self._exit_reason()}")
            if "method" in msg and "id" in msg:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
                continue
            if msg.get("id") != rid:
                continue
            return _rpc_result(msg, method)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def close(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def _read_sse(response: Any, rid: int) -> dict[str, Any] | None:
    data: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data:
                try:
                    msg = json.loads("\n".join(data))
                except ValueError:
                    msg = None
                if (
                    isinstance(msg, dict)
                    and msg.get("id") == rid
                    and ("result" in msg or "error" in msg)
                ):
                    return msg
            data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    return None


class HttpTransport(_Transport):
    kind = "http"

    def __init__(
        self, url: str, headers: dict[str, str] | None = None, allow_http: bool = False
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise McpConnectionError(f"--url {url}: expected an http or https URL")
        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS and not allow_http:
            raise McpConnectionError(
                f"--url {url}: plain http is only accepted for localhost; use https or --allow-http"
            )
        self.url = url
        self.target = url
        self.headers = dict(headers or {})
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._next_id = 1

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        h.update(self.headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    def _post(self, body: dict[str, Any], timeout: float) -> Any:
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), headers=self._headers(), method="POST"
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise McpError(f"{body.get('method')}: HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise McpConnectionError(f"cannot reach {self.url}: {exc}") from exc

    def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        rid = self._next_id
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        with self._post(body, timeout) as response:
            session = response.headers.get("Mcp-Session-Id")
            if method == "initialize" and session:
                self.session_id = session
            ctype = response.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                msg = _read_sse(response, rid)
            else:
                text = response.read().decode("utf-8", errors="replace")
                try:
                    msg = json.loads(text) if text.strip() else None
                except ValueError as exc:
                    raise McpError(f"{method}: response body is not JSON") from exc
        if not isinstance(msg, dict):
            raise McpError(f"{method}: no JSON-RPC response")
        return _rpc_result(msg, method)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        with self._post(body, DEFAULT_TIMEOUT) as response:
            response.read()

    def close(self) -> None:
        if not self.session_id:
            return
        req = urllib.request.Request(self.url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                response.read()
        except (urllib.error.URLError, OSError):
            pass


@dataclass
class McpClient:
    """A connected MCP client. Use :func:`connect` to make one, and close it when done."""

    transport: _Transport
    timeout: float = DEFAULT_TIMEOUT
    server: ServerInfo = field(default_factory=ServerInfo)

    def initialize(self) -> ServerInfo:
        result = self.transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agentdojo-mcp", "version": __version__},
            },
            self.timeout,
        )
        if not isinstance(result, dict):
            raise McpError("initialize: result is not an object")
        info = result.get("serverInfo") or {}
        self.server = ServerInfo(
            name=str(info.get("name", "")),
            version=str(info.get("version", "")),
            protocol_version=str(result.get("protocolVersion", "")),
            transport=self.transport.kind,
            target=self.transport.target,
        )
        if isinstance(self.transport, HttpTransport):
            self.transport.protocol_version = self.server.protocol_version or PROTOCOL_VERSION
        self.transport.notify("notifications/initialized")
        return self.server

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(1000):
            params = {"cursor": cursor} if cursor else None
            result = self.transport.request("tools/list", params, self.timeout)
            page = result.get("tools") if isinstance(result, dict) else None
            if not isinstance(page, list):
                raise McpError("tools/list: result has no tools array")
            tools.extend(t for t in page if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise McpError("tools/list: more than 1000 pages; giving up")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.transport.request(
            "tools/call", {"name": name, "arguments": arguments}, self.timeout
        )
        if not isinstance(result, dict):
            raise McpError(f"tools/call {name}: result is not an object")
        return result

    def dump(self) -> dict[str, Any]:
        """The server dump that ``map`` and ``validate-mapping`` read."""
        return {"server": self.server.as_dict(), "tools": self.list_tools()}

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> McpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect(
    stdio: str | list[str] | None = None,
    url: str | None = None,
    env: list[str] | None = None,
    headers: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    allow_http: bool = False,
) -> McpClient:
    """Start or reach a server and run the initialize handshake."""
    if (stdio is None) == (url is None):
        raise McpError("give exactly one of --stdio or --url")
    transport: _Transport
    if stdio is not None:
        transport = StdioTransport(stdio, child_environment(env))
    else:
        assert url is not None
        transport = HttpTransport(url, parse_headers(headers), allow_http=allow_http)
    client = McpClient(transport, timeout=timeout)
    try:
        client.initialize()
    except BaseException:
        client.close()
        raise
    return client


def result_text(result: dict[str, Any]) -> str:
    """Join the text blocks of a ``tools/call`` result; other block types are summarised."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(f"[{block.get('type', 'unknown')} content]")
    return "\n".join(parts)
