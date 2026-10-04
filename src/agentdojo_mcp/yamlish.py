"""Read and write the small YAML subset that mapping files use, with the standard library only.

Supported: block mappings and block lists nested by indentation, ``# comments``, plain scalars
(strings, integers, floats, ``true``/``false``/``null``), double-quoted strings with JSON escapes,
single-quoted strings, and flow values written as JSON (``{}``, ``[]``, ``{"a": 1}``). Anchors,
multi-line strings and tags are not supported; a file that needs them can be written as JSON,
which ``load`` also accepts.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLAIN_SAFE = re.compile(r"^[A-Za-z_./][A-Za-z0-9_./@+-]*( [A-Za-z0-9_./@+-]+)*$")
_NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "~", ""}


class YamlError(ValueError):
    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


def scalar(value: Any) -> str:
    """Render one scalar (or a flow JSON value for containers) as YAML text."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        if _PLAIN_SAFE.match(value) and value.lower() not in _RESERVED and not _NUMBER.match(value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def dump(data: Any, indent: int = 0) -> str:
    """Render dicts and lists as block YAML; empty containers and nested scalars inline."""
    lines: list[str] = []
    _dump(data, indent, lines)
    return "\n".join(lines) + "\n"


def _dump(data: Any, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            k = scalar(str(key))
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{k}:")
                _dump(value, indent + 2, lines)
            else:
                lines.append(f"{pad}{k}: {scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}-")
                _dump(item, indent + 2, lines)
            else:
                lines.append(f"{pad}- {scalar(item)}")
    else:
        lines.append(f"{pad}{scalar(data)}")


def _strip_comment(text: str) -> str:
    """Remove a trailing comment that is outside quotes."""
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(text):
        if quote == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def parse_scalar(text: str, line: int | None = None) -> Any:
    text = text.strip()
    if text == "" or text in ("null", "~"):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text[0] in '{["':
        try:
            return json.loads(text)
        except ValueError as exc:
            raise YamlError(f"cannot parse {text!r} as a JSON flow value: {exc}", line) from exc
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'":
            raise YamlError(f"unterminated single-quoted string {text!r}", line)
        return text[1:-1].replace("''", "'")
    if _NUMBER.match(text):
        return float(text) if any(c in text for c in ".eE") else int(text)
    if text[0] in "&*!|>%@`":
        raise YamlError(f"unsupported YAML syntax {text!r}; write the file as JSON instead", line)
    return text


def _split_key(content: str, line: int) -> tuple[str, str]:
    if content[0] in "\"'":
        quote = content[0]
        end = content.find(quote, 1)
        while quote == '"' and end > 0 and content[end - 1] == "\\":
            end = content.find(quote, end + 1)
        if end < 0 or not content[end + 1 :].startswith(":"):
            raise YamlError(f"expected 'key: value', got {content!r}", line)
        key = parse_scalar(content[: end + 1], line)
        return str(key), content[end + 2 :]
    match = re.match(r"^([^:#]+?):(\s|$)(.*)$", content)
    if not match:
        raise YamlError(f"expected 'key: value', got {content!r}", line)
    return match.group(1).strip(), match.group(3)


def load(text: str) -> Any:
    """Parse JSON or the YAML subset described in the module docstring."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise YamlError(f"invalid JSON: {exc}") from exc
    rows: list[tuple[int, int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("tabs are not allowed for indentation", number)
        content = _strip_comment(raw)
        if not content.strip() or content.strip() == "---":
            continue
        rows.append((number, len(content) - len(content.lstrip(" ")), content.strip()))
    if not rows:
        return None
    value, pos = _block(rows, 0, rows[0][1])
    if pos != len(rows):
        raise YamlError("unexpected indentation", rows[pos][0])
    return value


def _block(rows: list[tuple[int, int, str]], pos: int, indent: int) -> tuple[Any, int]:
    if rows[pos][2].startswith("-") and (rows[pos][2] == "-" or rows[pos][2][1] == " "):
        return _list(rows, pos, indent)
    return _mapping(rows, pos, indent)


def _child(rows: list[tuple[int, int, str]], pos: int, indent: int) -> tuple[Any, int]:
    """The nested block after a 'key:' or '-' line, or null when nothing is nested."""
    if pos < len(rows) and rows[pos][1] > indent:
        return _block(rows, pos, rows[pos][1])
    return None, pos


def _mapping(rows: list[tuple[int, int, str]], pos: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while pos < len(rows):
        number, ind, content = rows[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError("unexpected indentation", number)
        if content.startswith("- ") or content == "-":
            raise YamlError("list item where a mapping key was expected", number)
        key, rest = _split_key(content, number)
        if key in out:
            raise YamlError(f"duplicate key {key!r}", number)
        pos += 1
        if rest.strip():
            out[key] = parse_scalar(rest, number)
        else:
            out[key], pos = _child(rows, pos, indent)
    return out, pos


def _list(rows: list[tuple[int, int, str]], pos: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while pos < len(rows):
        number, ind, content = rows[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError("unexpected indentation", number)
        if not (content == "-" or content.startswith("- ")):
            raise YamlError("expected a list item", number)
        rest = content[1:].strip()
        pos += 1
        if not rest:
            item, pos = _child(rows, pos, indent)
        elif re.match(r"^[^\"'{\[][^:]*:(\s|$)", rest) or re.match(r"^\"[^\"]*\":(\s|$)", rest):
            # "- key: value" starts a mapping whose further keys sit two columns in.
            inner = [(number, indent + 2, rest)]
            end = pos
            while end < len(rows) and rows[end][1] > indent:
                end += 1
            item, used = _mapping(inner + rows[pos:end], 0, indent + 2)
            if used != 1 + end - pos:
                raise YamlError("unexpected indentation in list item", number)
            pos = end
        else:
            item = parse_scalar(rest, number)
        out.append(item)
    return out, pos
