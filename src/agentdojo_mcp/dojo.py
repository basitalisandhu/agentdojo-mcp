"""Everything that needs AgentDojo: loading suites, dumping their tool surface, profiling them.

AgentDojo is an optional dependency (``pip install "agentdojo-mcp[dojo]"``). This module imports
it only inside functions, so the rest of the package works without it.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from .schema import SUITE_DUMP_FORMAT

DEFAULT_BENCHMARK_VERSION = "v1.2.2"


class DojoMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            'this command needs AgentDojo. Install it with: pip install "agentdojo-mcp[dojo]"'
        )


class SuiteError(ValueError):
    pass


def require() -> Any:
    try:
        import agentdojo
    except ImportError as exc:
        raise DojoMissing() from exc
    return agentdojo


def agentdojo_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("agentdojo")
    except PackageNotFoundError:
        return "unknown"


def load_suite(spec: str, benchmark_version: str = DEFAULT_BENCHMARK_VERSION) -> Any:
    """A built-in suite name (``banking``), ``path/to/file.py:attr`` or ``package.module:attr``."""
    require()
    from agentdojo.task_suite.load_suites import get_suites
    from agentdojo.task_suite.task_suite import TaskSuite

    if ":" in spec:
        source, _, attr = spec.rpartition(":")
        if source.endswith(".py"):
            path = Path(source)
            if not path.is_file():
                raise SuiteError(f"--suite {spec}: {path} does not exist")
            module_name = "agentdojo_mcp_suite_" + "".join(
                c if c.isalnum() else "_" for c in path.stem
            )
            mod_spec = importlib.util.spec_from_file_location(module_name, path)
            if mod_spec is None or mod_spec.loader is None:
                raise SuiteError(f"--suite {spec}: cannot import {path}")
            module = importlib.util.module_from_spec(mod_spec)
            sys.modules[module_name] = module  # pydantic resolves annotations through it
            mod_spec.loader.exec_module(module)
        else:
            try:
                module = importlib.import_module(source)
            except ImportError as exc:
                raise SuiteError(f"--suite {spec}: cannot import {source}: {exc}") from exc
        suite = getattr(module, attr, None)
        if not isinstance(suite, TaskSuite):
            raise SuiteError(f"--suite {spec}: {attr} is not an AgentDojo TaskSuite")
        return suite
    suites = get_suites(benchmark_version)
    if not suites:
        raise SuiteError(f"AgentDojo has no benchmark version {benchmark_version!r}")
    if spec not in suites:
        raise SuiteError(
            f"--suite {spec}: not a {benchmark_version} suite (have: {', '.join(sorted(suites))})"
        )
    return suites[spec]


def mutating_tools(suite: Any) -> tuple[dict[str, bool], int]:
    """Replay every task's ground truth with AgentDojo's own runtime and note which tools changed
    the environment. Returns {tool: changed} for the tools that ran, and the number of calls that
    raised (those tools are left out unless another call ran them)."""
    from agentdojo.base_tasks import BaseUserTask
    from agentdojo.functions_runtime import FunctionsRuntime

    seen: dict[str, bool] = {}
    failures = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = suite.load_and_inject_default_environment({})
        runtime = FunctionsRuntime(suite.tools)
        for task in [*suite.user_tasks.values(), *suite.injection_tasks.values()]:
            env = base.model_copy(deep=True)
            if isinstance(task, BaseUserTask):
                env = task.init_environment(env)
            try:
                calls = task.ground_truth(env.model_copy(deep=True))
            except Exception:
                failures += 1
                continue
            for call in calls:
                before = env.model_dump_json()
                try:
                    runtime.run_function(env, call.function, call.args, raise_on_error=True)
                except Exception:
                    failures += 1
                    continue
                changed = env.model_dump_json() != before
                seen[call.function] = seen.get(call.function, False) or changed
    return seen, failures


def dump_suite(suite: Any, benchmark_version: str, profile: bool = True) -> dict[str, Any]:
    """The suite's tool surface as a JSON-able suite dump (the format ``map`` reads)."""
    mutates: dict[str, bool] = {}
    if profile:
        mutates, _ = mutating_tools(suite)
    tools = []
    for fn in suite.tools:
        schema = fn.parameters.model_json_schema()
        tools.append(
            {
                "name": fn.name,
                "description": fn.description,
                "inputSchema": json.loads(json.dumps(schema, default=str)),
                "dependencies": sorted(fn.dependencies),
                "mutates": mutates.get(fn.name),
            }
        )
    return {
        "format": SUITE_DUMP_FORMAT,
        "suite": suite.name,
        "benchmark_version": benchmark_version,
        "agentdojo_version": agentdojo_version(),
        "tools": tools,
    }


def profile_suites(benchmark_version: str) -> list[dict[str, Any]]:
    """Per built-in suite: sizes and which tools changed state in a ground-truth replay."""
    require()
    from agentdojo.task_suite.load_suites import get_suites

    rows = []
    for name, suite in sorted(get_suites(benchmark_version).items()):
        seen, failures = mutating_tools(suite)
        tool_names = [t.name for t in suite.tools]
        rows.append(
            {
                "suite": name,
                "tools": len(tool_names),
                "user_tasks": len(suite.user_tasks),
                "injection_tasks": len(suite.injection_tasks),
                "exercised": sorted(seen),
                "mutating": sorted(t for t, changed in seen.items() if changed),
                "read_only": sorted(t for t, changed in seen.items() if not changed),
                "not_exercised": sorted(set(tool_names) - set(seen)),
                "state": sorted({d for t in suite.tools for d in t.dependencies}),
                "replay_failures": failures,
            }
        )
    return rows
