"""Command-line interface: inspect, map, validate-mapping, run, replay, suites, dump-suite."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .mapping import MappingError, propose, validate
from .mapping import dumps as dump_mapping
from .mapping import load as load_mapping
from .mcp_client import DEFAULT_TIMEOUT, McpError, connect
from .report import FORMATS, render
from .results import ResultsError
from .results import dump as dump_results
from .results import load as load_results
from .schema import DumpError, Surface, load_server_dump, load_suite_dump, suite_surface

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

DESCRIPTION = (
    "Run AgentDojo against MCP servers: map an AgentDojo task suite's tools onto a real MCP "
    "server, run the benchmark's user tasks and injection tasks against the server's actual "
    "tool surface, and report utility and attack success per task with every call logged."
)
EPILOG = (
    'Commands that need AgentDojo (run, suites, dump-suite): pip install "agentdojo-mcp[dojo]".\n'
    "Exit codes: 0 ok, 1 validation errors (validate-mapping), 2 usage, I/O or server errors.\n"
    "Docs: https://github.com/basitalisandhu/agentdojo-mcp"
)


class _Formatter(argparse.RawDescriptionHelpFormatter):
    pass


def _server_args(p: argparse.ArgumentParser, required: bool = True) -> None:
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument(
        "--stdio", metavar="CMD", help='start a stdio server, e.g. --stdio "python server.py"'
    )
    g.add_argument("--url", metavar="URL", help="a Streamable HTTP endpoint")
    p.add_argument(
        "-e",
        "--env",
        action="append",
        metavar="KEY[=VALUE]",
        help="pass an environment variable to a stdio server (bare KEY copies it from yours); "
        "only PATH, HOME and a few basics are passed otherwise",
    )
    p.add_argument(
        "-H",
        "--header",
        action="append",
        metavar="'NAME: VALUE'",
        help="HTTP header for --url; a value written ${VAR} is read from the environment",
    )
    p.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per request (default 30)"
    )
    p.add_argument(
        "--allow-http", action="store_true", help="accept plain http for hosts other than localhost"
    )


def _suite_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--suite",
        required=True,
        metavar="SUITE",
        help="a suite dump (.json, from dump-suite), a built-in suite name such as banking "
        "(needs AgentDojo), or file.py:attr / module:attr for a custom TaskSuite",
    )
    p.add_argument(
        "--benchmark-version",
        default=None,
        metavar="V",
        help="AgentDojo benchmark version for built-in suites (default v1.2.2)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentdojo-mcp", description=DESCRIPTION, epilog=EPILOG, formatter_class=_Formatter
    )
    parser.add_argument("--version", action="version", version=f"agentdojo-mcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser(
        "inspect",
        help="list a server's tools and schemas",
        description="Connect to an MCP server, run initialize and tools/list, and print the tools. "
        "--json writes the server dump that map and validate-mapping read.",
        formatter_class=_Formatter,
    )
    _server_args(p)
    p.add_argument("--json", action="store_true", help="print the server dump as JSON")
    p.add_argument("-o", "--output", type=Path, help="write the server dump (JSON) to this file")

    p = sub.add_parser(
        "map",
        help="propose a mapping from suite tools to server tools",
        description="Score every server tool against every suite tool by name similarity and "
        "schema shape and write the best match per suite tool as an editable mapping file.",
        formatter_class=_Formatter,
    )
    _suite_arg(p)
    p.add_argument("--server-dump", type=Path, required=True, help="JSON from inspect --json")
    p.add_argument("-o", "--out", type=Path, help="write the mapping here (default: stdout)")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")

    p = sub.add_parser(
        "validate-mapping",
        help="check a mapping against the suite and the server",
        description="Check every entry of a mapping against the suite's tools and the server's "
        "tools: names, parameters, required arguments, types and modes.",
        formatter_class=_Formatter,
    )
    p.add_argument("mapping", type=Path, help="the mapping file (YAML or JSON)")
    _suite_arg(p)
    p.add_argument("--server-dump", type=Path, required=True, help="JSON from inspect --json")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument("--json", action="store_true", help="print findings as JSON")

    p = sub.add_parser(
        "run",
        help="run the suite's tasks against the server (needs AgentDojo)",
        description="Build an AgentDojo runtime whose tools call the server through the mapping, "
        "run the selected user tasks without and with injection tasks using AgentDojo's own "
        "benchmark functions, and write results.json and report.md.",
        formatter_class=_Formatter,
    )
    _suite_arg(p)
    p.add_argument("--mapping", type=Path, required=True, help="the mapping file")
    _server_args(p)
    p.add_argument(
        "--model",
        required=True,
        help="provider:model-name for any provider AgentDojo supports (openai, anthropic, google, "
        "cohere, together, local, vllm_parsed, openai-compatible), or ground-truth to replay "
        "each task's reference solution without a model",
    )
    p.add_argument("--model-id", help="model id for the local and openai-compatible providers")
    p.add_argument("--defense", help="an AgentDojo defense name, e.g. tool_filter")
    p.add_argument("--system-message-name", help="an AgentDojo system message name")
    p.add_argument(
        "--tasks",
        "--user-task",
        dest="tasks",
        action="append",
        metavar="ID",
        help="user task id or case-sensitive glob pattern (repeatable; default: all)",
    )
    p.add_argument(
        "--injection-task",
        action="append",
        metavar="ID",
        help="injection task id or case-sensitive glob pattern (repeatable; default: all)",
    )
    p.add_argument(
        "--attack",
        default="important_instructions",
        help="AgentDojo attack name (default important_instructions); 'none' runs utility only",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("agentdojo-mcp-results"),
        help="where results.json, report.md and traces/ go (default ./agentdojo-mcp-results)",
    )
    p.add_argument(
        "--no-validate", action="store_true", help="skip the mapping check before running"
    )

    p = sub.add_parser(
        "replay",
        help="re-render the report of a results file (offline)",
        description="Render a results.json from run as text, Markdown or a JSON summary. "
        "Nothing is executed and no network is used.",
        formatter_class=_Formatter,
    )
    p.add_argument("results", type=Path)
    p.add_argument("--format", choices=FORMATS, default="text")
    p.add_argument("-o", "--output", type=Path, help="write to a file instead of stdout")

    p = sub.add_parser(
        "suites",
        help="profile AgentDojo's built-in suites for bridging (needs AgentDojo)",
        description="Replay every task's ground truth with AgentDojo's own runtime and report, "
        "per suite, which tools changed the environment. Read-only tools bridge most easily.",
        formatter_class=_Formatter,
    )
    p.add_argument("--benchmark-version", default=None, metavar="V")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "dump-suite",
        help="write a suite's tool surface as JSON for offline mapping (needs AgentDojo)",
        formatter_class=_Formatter,
    )
    _suite_arg(p)
    p.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    return parser


def _err(message: str) -> int:
    print(f"agentdojo-mcp: error: {message}", file=sys.stderr)
    return EXIT_ERROR


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}", file=sys.stderr)


def _connect(args: argparse.Namespace) -> Any:
    return connect(
        stdio=args.stdio,
        url=args.url,
        env=args.env,
        headers=args.header,
        timeout=args.timeout,
        allow_http=args.allow_http,
    )


def _suite_surface(args: argparse.Namespace) -> tuple[Surface, str, str | None]:
    """The suite side: a dump file offline, or AgentDojo for names and file.py:attr specs."""
    spec = args.suite
    if spec.endswith(".json"):
        surface = load_suite_dump(Path(spec))
        return surface, str(surface.meta.get("suite") or ""), surface.meta.get("benchmark_version")
    from .dojo import DEFAULT_BENCHMARK_VERSION, dump_suite, load_suite

    bv = args.benchmark_version or DEFAULT_BENCHMARK_VERSION
    suite = load_suite(spec, bv)
    return suite_surface(dump_suite(suite, bv), label=spec), suite.name, bv


def cmd_inspect(args: argparse.Namespace) -> int:
    with _connect(args) as client:
        dump = client.dump()
    if args.output:
        _write(json.dumps(dump, indent=2, ensure_ascii=False) + "\n", args.output)
    if args.json:
        sys.stdout.write(json.dumps(dump, indent=2, ensure_ascii=False) + "\n")
        return EXIT_OK
    server = dump["server"]
    print(
        f"{server['name'] or '(unnamed)'} {server['version']}  protocol {server['protocolVersion']}  "
        f"{server['transport']}  {len(dump['tools'])} tools"
    )
    for tool in dump["tools"]:
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        params = ", ".join(
            f"{name}{'' if name in required else '?'}: {(p or {}).get('type', 'any') if isinstance(p, dict) else 'any'}"
            for name, p in props.items()
        )
        print(f"  {tool['name']}({params})")
        desc = (tool.get("description") or "").strip().splitlines()
        if desc:
            print(f"      {desc[0][:96]}")
    return EXIT_OK


def cmd_map(args: argparse.Namespace) -> int:
    if args.out and args.out.exists() and not args.force:
        return _err(f"{args.out} exists; pass --force to overwrite it")
    suite, name, bv = _suite_surface(args)
    server = load_server_dump(args.server_dump)
    mapping = propose(suite, server, name, bv)
    text = dump_mapping(mapping)
    _write(text, args.out)
    counts = mapping.modes()
    print(
        f"proposed {counts['server'] + counts['both']} of {len(mapping.tools)} suite tools "
        f"({counts['server']} server, {counts['both']} both, {counts['local']} local); "
        "edit the file, then run validate-mapping",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    suite, _name, _bv = _suite_surface(args)
    server = load_server_dump(args.server_dump)
    findings = validate(mapping, suite, server)
    errors = [f for f in findings if f.level == "error"]
    warnings_ = [f for f in findings if f.level == "warning"]
    if args.json:
        doc = {
            "ok": not errors and not (args.strict and warnings_),
            "errors": len(errors),
            "warnings": len(warnings_),
            "findings": [f.__dict__ for f in findings],
        }
        print(json.dumps(doc, indent=2))
    else:
        for f in findings:
            print(f)
        modes = mapping.modes()
        print(
            f"{len(errors)} errors, {len(warnings_)} warnings; {len(mapping.tools)} tools "
            f"({modes['server']} server, {modes['both']} both, {modes['local']} local)"
        )
    failed = bool(errors) or (args.strict and bool(warnings_))
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from .bridge import model_pipeline_factory, run_benchmark
    from .dojo import DEFAULT_BENCHMARK_VERSION, dump_suite, load_suite, require

    require()
    mapping = load_mapping(args.mapping)
    bv = args.benchmark_version or mapping.benchmark_version or DEFAULT_BENCHMARK_VERSION
    suite = load_suite(args.suite, bv)
    if suite.name != mapping.suite:
        return _err(f"mapping is for suite {mapping.suite!r}, --suite is {suite.name!r}")
    factory, pipeline_name = model_pipeline_factory(
        args.model,
        model_id=args.model_id,
        defense=args.defense,
        system_message_name=args.system_message_name,
    )
    attack = None if args.attack in ("none", "") else args.attack
    with _connect(args) as client:
        if not args.no_validate:
            from .schema import server_surface

            server = server_surface(
                {"server": client.server.as_dict(), "tools": client.list_tools()}
            )
            findings = validate(mapping, suite_surface(dump_suite(suite, bv)), server)
            errors = [f for f in findings if f.level == "error"]
            for f in findings:
                print(f, file=sys.stderr)
            if errors:
                return _err(f"the mapping has {len(errors)} errors; fix them or pass --no-validate")
        out_dir: Path = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        data = run_benchmark(
            suite,
            mapping,
            client,
            factory,
            pipeline_name,
            benchmark_version=bv,
            user_tasks=args.tasks,
            injection_tasks=args.injection_task,
            attack=attack,
            mapping_file=str(args.mapping),
            model_label=args.model,
            trace_dir=out_dir / "traces",
            progress=lambda m: print(f"  {m}", file=sys.stderr),
        )
    dump_results(data, out_dir / "results.json")
    (out_dir / "report.md").write_text(render(data, "markdown"), encoding="utf-8")
    sys.stdout.write(render(data, "text"))
    print(f"wrote {out_dir / 'results.json'} and {out_dir / 'report.md'}", file=sys.stderr)
    return EXIT_OK


def cmd_replay(args: argparse.Namespace) -> int:
    data = load_results(args.results)
    _write(render(data, args.format), args.output)
    return EXIT_OK


def cmd_suites(args: argparse.Namespace) -> int:
    from .dojo import DEFAULT_BENCHMARK_VERSION, profile_suites

    bv = args.benchmark_version or DEFAULT_BENCHMARK_VERSION
    rows = profile_suites(bv)
    if args.json:
        print(json.dumps({"benchmark_version": bv, "suites": rows}, indent=2))
        return EXIT_OK
    print(f"AgentDojo {bv}: tools that changed the environment when each task's ground truth ran")
    for r in rows:
        print(
            f"\n{r['suite']}: {r['tools']} tools, {r['user_tasks']} user tasks, "
            f"{r['injection_tasks']} injection tasks; state: {', '.join(r['state'])}"
        )
        print(f"  changed state ({len(r['mutating'])}): {', '.join(r['mutating']) or '-'}")
        print(f"  read only     ({len(r['read_only'])}): {', '.join(r['read_only']) or '-'}")
        print(
            f"  not exercised ({len(r['not_exercised'])}): {', '.join(r['not_exercised']) or '-'}"
        )
        if r["replay_failures"]:
            print(f"  ground-truth calls that raised: {r['replay_failures']}")
    return EXIT_OK


def cmd_dump_suite(args: argparse.Namespace) -> int:
    from .dojo import DEFAULT_BENCHMARK_VERSION, dump_suite, load_suite

    bv = args.benchmark_version or DEFAULT_BENCHMARK_VERSION
    suite = load_suite(args.suite, bv)
    _write(json.dumps(dump_suite(suite, bv), indent=2, ensure_ascii=False) + "\n", args.output)
    return EXIT_OK


COMMANDS = {
    "inspect": cmd_inspect,
    "map": cmd_map,
    "validate-mapping": cmd_validate,
    "run": cmd_run,
    "replay": cmd_replay,
    "suites": cmd_suites,
    "dump-suite": cmd_dump_suite,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR
    from .bridge import BridgeError  # AgentDojo itself is imported lazily inside bridge functions
    from .dojo import DojoMissing, SuiteError

    try:
        return COMMANDS[args.command](args)
    except DojoMissing as exc:
        return _err(str(exc))
    except (McpError, MappingError, DumpError, ResultsError, SuiteError, BridgeError) as exc:
        return _err(str(exc))
    except KeyboardInterrupt:
        return _err("interrupted")
