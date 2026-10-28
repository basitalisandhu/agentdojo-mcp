"""Scaling checks: ten times the input should take roughly ten times as long, not a hundred.

Each test times a base size and ten times that size (best of a few runs) and fails if the ratio
is beyond what linear work plus noise explains. The bound (35) sits between linear (about 10)
and quadratic (about 100), so a slow CI machine does not fail it but a quadratic regression does.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from agentdojo_mcp import mapping, yamlish
from agentdojo_mcp.bridge import pick_tasks
from agentdojo_mcp.report import render
from agentdojo_mcp.results import diff
from agentdojo_mcp.schema import Param, Surface, ToolSpec

LINEAR_BOUND = 35


def best_time(fn: Callable[[], object], repeat: int = 3) -> float:
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return max(best, 1e-6)


def ratio(make: Callable[[int], Callable[[], object]], n: int) -> float:
    small, large = make(n), make(10 * n)
    small()  # warm up
    return best_time(large) / best_time(small)


def surface(n: int, prefix: str) -> Surface:
    return Surface(
        prefix,
        {
            f"{prefix}_get_item_{i}": ToolSpec(
                f"{prefix}_get_item_{i}",
                f"Get item number {i}.",
                {"item_id": Param("item_id", frozenset({"string"}), True)},
            )
            for i in range(n)
        },
    )


def test_task_selection_with_many_plain_ids_is_linear():
    def make(n: int):
        available = {f"user_task_{i}": i for i in range(n)}
        ids = list(available)
        return lambda: pick_tasks(ids, available, "user tasks", "s")

    assert ratio(make, 300) < LINEAR_BOUND


def test_propose_is_linear_in_server_tools():
    suite = surface(10, "suite")

    def make(n: int):
        server = surface(n, "srv")
        return lambda: mapping.propose(suite, server, "s", None)

    assert ratio(make, 30) < LINEAR_BOUND


def test_mapping_load_and_validate_are_linear():
    def make(n: int):
        suite, server = surface(n, "suite"), surface(n, "srv")
        m = mapping.Mapping(
            suite="s",
            tools={
                name: mapping.ToolMap(suite_tool=name, server_tool=f"srv{name[5:]}", mode="server")
                for name in suite.tools
            },
        )
        text = mapping.dumps(m)
        return lambda: mapping.validate(mapping.from_data(yamlish.load(text)), suite, server)

    assert ratio(make, 200) < LINEAR_BOUND


def _results(n: int, flip: bool = False) -> dict:
    return {
        "tool": "agentdojo-mcp",
        "format": 1,
        "user_tasks": [{"id": f"u{i}", "utility": (i % 2 == 0) != flip} for i in range(n)],
        "injection_tasks": [{"id": "i0", "utility": True}],
        "pairs": [
            {"user_task": f"u{i}", "injection_task": "i0", "utility": True, "attack_success": flip}
            for i in range(n)
        ],
        "calls": [
            {"seq": i, "user_task": f"u{i}", "suite_tool": f"t{i % 30}", "arguments": {"a": i}}
            for i in range(n)
        ],
    }


def test_report_and_diff_are_linear():
    def make(n: int):
        new, old = _results(n, True), _results(n)
        return lambda: (render(new, "markdown"), diff(new, old))

    assert ratio(make, 300) < LINEAR_BOUND
