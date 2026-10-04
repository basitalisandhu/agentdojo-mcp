#!/usr/bin/env python3
"""A tiny MCP server over stdio for the tests: a notebook with four tools.

Tools: notes_list, notes_get(name), notes_create(name, text), load_state(state). load_state takes
the AgentDojo environment of the fixture suite ({"notebook": {"notes": [...]}}) so the bridge can
seed it before each task. Flags change its behaviour for transport tests:

  --paginate        tools/list returns two tools per page with nextCursor
  --noise           print a line that is not JSON on stdout before anything else
  --exit-on-init    exit without answering initialize
  --server-request  send a roots/list request to the client before answering initialize
  --slow            tools/call sleeps 3 seconds before answering

Standard library only. handle() is also used by the HTTP fixture in tests/conftest.py.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "notes_list",
        "description": "List the names of all notes.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "notes_get",
        "description": "Return the text of the note with the given name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Note name."}},
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "notes_create",
        "description": "Create a note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Note name."},
                "text": {"type": "string", "description": "Note text."},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "load_state",
        "description": "Replace the notebook with the given state (test seeding).",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "object", "description": "Environment dump."}},
            "required": ["state"],
        },
    },
]

NOTES: dict[str, str] = {"groceries": "milk, eggs"}
FLAGS = set(sys.argv[1:])


def text(value: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": value}]}
    if is_error:
        result["isError"] = True
    return result


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "notes_list":
        return text(json.dumps(sorted(NOTES)))
    if name == "notes_get":
        if args.get("name") not in NOTES:
            return text(f"no note named {args.get('name')!r}", is_error=True)
        return text(NOTES[args["name"]])
    if name == "notes_create":
        if not isinstance(args.get("name"), str) or not isinstance(args.get("text"), str):
            return text("name and text are required strings", is_error=True)
        NOTES[args["name"]] = args["text"]
        return text(f"created {args['name']}")
    if name == "load_state":
        state = args.get("state") or {}
        notes = (state.get("notebook") or {}).get("notes") or []
        NOTES.clear()
        NOTES.update({n["title"]: n["body"] for n in notes})
        return text(f"loaded {len(NOTES)} notes")
    raise KeyError(name)


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result: Any = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture-notes", "version": "0.1.0"},
        }
    elif method == "tools/list":
        if "--paginate" in FLAGS:
            start = int((msg.get("params") or {}).get("cursor") or 0)
            result = {"tools": TOOLS[start : start + 2]}
            if start + 2 < len(TOOLS):
                result["nextCursor"] = str(start + 2)
        else:
            result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if "--slow" in FLAGS:
            time.sleep(3)
        try:
            result = call(params.get("name"), params.get("arguments") or {})
        except KeyError:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": "unknown tool"},
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": "Method not found"},
        }
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    if "--noise" in FLAGS:
        print("fixture server starting", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            if "--exit-on-init" in FLAGS:
                print("fixture server: exiting on purpose", file=sys.stderr, flush=True)
                sys.exit(3)
            if "--server-request" in FLAGS:
                send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
        if "method" not in msg:
            continue  # a response to our own request
        response = handle(msg)
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
