"""The bridge: an AgentDojo suite whose tools call an MCP server through a mapping.

How a run works:

1. The suite is copied and every mapped tool is replaced by a :class:`Function` with the same
   name, description and parameters, whose body calls the server tool named in the mapping
   (``server`` mode), or runs AgentDojo's implementation for the environment and then calls the
   server (``both`` mode). ``local`` tools are left as they are. The agent therefore sees the
   suite's tool names, so AgentDojo's utility and security checks work unchanged.
2. If the mapping has a ``seed`` tool, the agent pipeline is wrapped so that before each task the
   (possibly injected) environment is sent to the server. That is how AgentDojo's injections reach
   a server that holds its own data.
3. User tasks, injection tasks and their pairs are run with AgentDojo's own
   ``run_task_without_injection_tasks`` and ``run_task_with_injection_tasks``, one task or pair at a
   time so every MCP call can be attributed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .dojo import agentdojo_version, require
from .mapping import Mapping, Seed, ToolMap
from .mcp_client import McpClient, McpConnectionError, McpError, result_text
from .results import RESULTS_FORMAT

GROUND_TRUTH = "ground-truth"


class BridgeError(RuntimeError):
    pass


class McpToolError(Exception):
    """The server tool returned isError; AgentDojo hands the message to the agent."""


@dataclass
class CallLog:
    calls: list[dict[str, Any]] = field(default_factory=list)
    user_task: str | None = None
    injection_task: str | None = None

    def add(self, **entry: Any) -> None:
        self.calls.append(
            {
                "seq": len(self.calls) + 1,
                "user_task": self.user_task,
                "injection_task": self.injection_task,
                **entry,
            }
        )


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def convert_result(result: dict[str, Any], how: str) -> Any:
    """The value the AgentDojo tool returns, from an MCP tools/call result."""
    structured = result.get("structuredContent")
    text = result_text(result)
    if how == "auto" and isinstance(structured, dict):
        return structured
    if how == "json":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def bridged_function(original: Any, tm: ToolMap, client: McpClient, log: CallLog) -> Any:
    """A Function with the original's surface whose body calls the mapped server tool."""
    from agentdojo.functions_runtime import Function

    deps = set(original.dependencies)
    server_tool = tm.server_tool
    assert server_tool

    def run(**kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if k not in deps}
        if tm.mode == "both":
            original.run(**kwargs)  # keeps AgentDojo's environment in step for scoring
        server_args = _jsonable(tm.translate(args))
        start = time.monotonic()
        try:
            result = client.call_tool(server_tool, server_args)
        except McpConnectionError:
            raise
        except McpError as exc:
            log.add(
                suite_tool=tm.suite_tool,
                server_tool=server_tool,
                mode=tm.mode,
                arguments=server_args,
                result_bytes=0,
                is_error=True,
                error=str(exc),
                ms=round(1000 * (time.monotonic() - start)),
            )
            raise McpToolError(str(exc)) from exc
        size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        is_error = bool(result.get("isError"))
        log.add(
            suite_tool=tm.suite_tool,
            server_tool=server_tool,
            mode=tm.mode,
            arguments=server_args,
            result_bytes=size,
            is_error=is_error,
            error=result_text(result)[:200] if is_error else None,
            ms=round(1000 * (time.monotonic() - start)),
        )
        if is_error:
            raise McpToolError(result_text(result) or f"{server_tool} returned an error")
        return convert_result(result, tm.result)

    return Function(
        name=original.name,
        description=original.description,
        parameters=original.parameters,
        dependencies=original.dependencies,
        run=run,
        full_docstring=original.full_docstring,
        return_type=original.return_type,
    )


def bridged_suite(suite: Any, mapping: Mapping, client: McpClient, log: CallLog) -> Any:
    """A shallow copy of the suite with mapped tools replaced; tasks and data are shared."""
    names = {t.name for t in suite.tools}
    unknown = sorted(set(mapping.tools) - names)
    if unknown:
        raise BridgeError(f"mapping names tools the suite does not have: {', '.join(unknown)}")
    tools = []
    for fn in suite.tools:
        tm = mapping.tools.get(fn.name)
        if tm is None or tm.mode == "local":
            tools.append(fn)
        else:
            tools.append(bridged_function(fn, tm, client, log))
    copy_ = copy.copy(suite)
    copy_.tools = tools
    return copy_


def seeding_pipeline(inner: Any, client: McpClient, seed: Seed | None, log: CallLog) -> Any:
    """Wrap a pipeline so the task environment is sent to the server's seed tool first."""
    from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
    from agentdojo.functions_runtime import EmptyEnv

    class SeedThenRun(BasePipelineElement):
        def __init__(self) -> None:
            self.name = inner.name

        def query(self, query, runtime, env=None, messages=(), extra_args=None):  # type: ignore[no-untyped-def]
            if env is None:
                env = EmptyEnv()
            if seed is not None:
                state = _jsonable(env.model_dump(mode="json"))
                size = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
                start = time.monotonic()
                result = client.call_tool(seed.tool, {seed.argument: state})
                is_error = bool(result.get("isError"))
                log.add(
                    suite_tool="(seed)",
                    server_tool=seed.tool,
                    mode="seed",
                    # The environment holds the injected text; record its size, not its content.
                    arguments={seed.argument: f"<suite environment, {size} bytes>"},
                    result_bytes=len(json.dumps(result).encode("utf-8")),
                    is_error=is_error,
                    error=result_text(result)[:200] if is_error else None,
                    ms=round(1000 * (time.monotonic() - start)),
                )
                if is_error:
                    raise BridgeError(f"seed tool {seed.tool} failed: {result_text(result)[:200]}")
            return inner.query(query, runtime, env, list(messages), dict(extra_args or {}))

    return SeedThenRun()


PipelineFactory = Callable[[Any], Any]


def model_pipeline_factory(
    model: str,
    model_id: str | None = None,
    defense: str | None = None,
    system_message_name: str | None = None,
    tool_delimiter: str = "tool",
) -> tuple[PipelineFactory, str]:
    """``ground-truth`` replays each task's reference solution (no model, no network);
    ``provider:model`` builds AgentDojo's pipeline for that provider. API keys are read by
    AgentDojo and the provider SDKs from their documented environment variables."""
    require()
    if model == GROUND_TRUTH:
        from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline

        def factory(task: Any) -> Any:
            p = GroundTruthPipeline(task)
            p.name = GROUND_TRUTH
            return p

        return factory, GROUND_TRUTH
    if ":" not in model:
        raise BridgeError(f"--model {model}: expected provider:model or {GROUND_TRUTH}")
    provider, name = model.split(":", 1)
    from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig, get_llm

    try:
        llm = get_llm(provider, name, model_id, tool_delimiter)
    except ValueError as exc:
        raise BridgeError(f"--model {model}: {exc}") from exc
    llm.name = name
    pipeline = AgentPipeline.from_config(
        PipelineConfig(
            llm=llm,
            model_id=model_id,
            defense=defense,
            system_message_name=system_message_name,
            system_message=None,
        )
    )
    pipeline.name = name if defense is None else f"{name}-{defense}"
    return (lambda _task: pipeline), pipeline.name


class _QuietLogger:
    """Collects AgentDojo's per-task traces under our output directory without printing them."""

    def __new__(cls, logdir: Path | None) -> Any:
        from agentdojo.logging import NullLogger

        class Quiet(NullLogger):
            def __enter__(self):  # type: ignore[no-untyped-def]
                from agentdojo.logging import LOGGER_STACK

                LOGGER_STACK.get().append(self)
                self.messages = []
                self.logdir = str(logdir) if logdir else None
                return self

            def __exit__(self, *exc):  # type: ignore[no-untyped-def]
                from agentdojo.logging import LOGGER_STACK

                LOGGER_STACK.get().pop()
                return False

            def log(self, messages, **kwargs):  # type: ignore[no-untyped-def]
                self.messages = list(messages)

            def log_error(self, message):  # type: ignore[no-untyped-def]
                self.errors.append(str(message))

        q = Quiet()
        q.errors = []
        return q


def run_benchmark(
    suite: Any,
    mapping: Mapping,
    client: McpClient,
    factory: PipelineFactory,
    pipeline_name: str,
    *,
    benchmark_version: str,
    user_tasks: Sequence[str] | None = None,
    injection_tasks: Sequence[str] | None = None,
    attack: str | None = "important_instructions",
    mapping_file: str | None = None,
    model_label: str | None = None,
    trace_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the selected tasks through the bridge and return a results document (format 1)."""
    require()
    import agentdojo.attacks  # noqa: F401  (registers the attacks)
    from agentdojo.attacks.attack_registry import ATTACKS, load_attack
    from agentdojo.benchmark import run_task_with_injection_tasks, run_task_without_injection_tasks

    say = progress or (lambda _m: None)
    if trace_dir is None:
        # AgentDojo writes a trace per task; without a directory it would write inside its own
        # installation, so keep them in a temporary directory that is removed afterwards.
        with tempfile.TemporaryDirectory(prefix="agentdojo-mcp-") as tmp:
            return run_benchmark(
                suite,
                mapping,
                client,
                factory,
                pipeline_name,
                benchmark_version=benchmark_version,
                user_tasks=user_tasks,
                injection_tasks=injection_tasks,
                attack=attack,
                mapping_file=mapping_file,
                model_label=model_label,
                trace_dir=Path(tmp),
                progress=progress,
            )
    log = CallLog()
    notes: list[str] = []
    bsuite = bridged_suite(suite, mapping, client, log)

    def pick(ids: Sequence[str] | None, available: dict[str, Any], kind: str) -> list[Any]:
        if not ids:
            return list(available.values())
        missing = [i for i in ids if i not in available]
        if missing:
            raise BridgeError(f"{kind} not in suite {suite.name}: {', '.join(missing)}")
        return [available[i] for i in ids]

    uts = pick(user_tasks, suite.user_tasks, "user tasks")
    its = pick(injection_tasks, suite.injection_tasks, "injection tasks") if attack else []

    attack_obj = None
    if attack:
        if attack not in ATTACKS:
            raise BridgeError(f"--attack {attack}: unknown (have: {', '.join(sorted(ATTACKS))})")
        probe = factory(uts[0]) if uts else None
        if probe is not None:
            probe.name = pipeline_name
        try:
            attack_obj = load_attack(attack, suite, probe)
        except ValueError as exc:
            raise BridgeError(
                f"--attack {attack}: {exc}. Attacks that address the model by name need a model "
                "AgentDojo recognises; use one that does not, such as direct or ignore_previous"
            ) from exc

    def wrapped(task: Any) -> Any:
        p = seeding_pipeline(factory(task), client, mapping.seed, log)
        p.name = pipeline_name
        return p

    def guarded(label: str, fn: Callable[[], tuple[bool, bool]]) -> tuple[bool | None, bool | None]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return fn()
        except McpConnectionError:
            raise
        except Exception as exc:
            notes.append(f"{label}: {type(exc).__name__}: {str(exc)[:300]}")
            return None, None

    user_rows, injection_rows, pair_rows = [], [], []
    with _QuietLogger(trace_dir) as qlog:
        for ut in uts:
            log.user_task, log.injection_task = ut.ID, None
            say(f"{ut.ID}")
            utility, _ = guarded(
                ut.ID,
                lambda ut=ut: run_task_without_injection_tasks(
                    bsuite, wrapped(ut), ut, trace_dir, True, benchmark_version
                ),
            )
            user_rows.append({"id": ut.ID, "utility": utility})
        for it in its:
            log.user_task, log.injection_task = it.ID, None
            say(f"{it.ID} as a user task")
            utility, _ = guarded(
                it.ID,
                lambda it=it: run_task_without_injection_tasks(
                    bsuite, wrapped(it), it, trace_dir, True, benchmark_version
                ),
            )
            injection_rows.append({"id": it.ID, "utility": utility})
        for ut in uts if attack_obj else []:
            for it in its:
                log.user_task, log.injection_task = ut.ID, it.ID
                say(f"{ut.ID} + {it.ID}")

                def one(ut: Any = ut, it: Any = it) -> tuple[bool, bool]:
                    u, s = run_task_with_injection_tasks(
                        bsuite,
                        wrapped(ut),
                        ut,
                        attack_obj,
                        trace_dir,
                        True,
                        [it.ID],
                        benchmark_version,
                    )
                    key = (ut.ID, it.ID)
                    if key not in u:  # DoS attacks run one injection task whatever we ask
                        key = next(iter(u))
                    return u[key], s[key]

                utility, success = guarded(f"{ut.ID} + {it.ID}", one)
                pair_rows.append(
                    {
                        "user_task": ut.ID,
                        "injection_task": it.ID,
                        "utility": utility,
                        "attack_success": success,
                    }
                )
        notes += [f"AgentDojo: {e}" for e in qlog.errors]

    mapping_hash = None
    if mapping_file and Path(mapping_file).is_file():
        mapping_hash = hashlib.sha256(Path(mapping_file).read_bytes()).hexdigest()
    return {
        "tool": "agentdojo-mcp",
        "version": __version__,
        "format": RESULTS_FORMAT,
        "suite": suite.name,
        "benchmark_version": benchmark_version,
        "agentdojo_version": agentdojo_version(),
        "model": model_label or pipeline_name,
        "attack": attack,
        "server": client.server.as_dict(),
        "mapping": {
            "file": Path(mapping_file).name if mapping_file else None,
            "sha256": mapping_hash,
            "seed": mapping.seed.tool if mapping.seed else None,
            "tools": {
                name: {"server_tool": tm.server_tool, "mode": tm.mode}
                for name, tm in sorted(mapping.tools.items())
            },
        },
        "user_tasks": user_rows,
        "injection_tasks": injection_rows,
        "pairs": pair_rows,
        "calls": log.calls,
        "notes": notes,
    }
