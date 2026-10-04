#!/usr/bin/env python3
"""Regenerate tests/fixtures/fixture-results.json, the results file the demo and tests replay.

Runs the fixture suite (tests/fixtures/fixture-dojo-suite) against the fixture MCP server
(tests/fixtures/fixture-server.py) through tests/fixtures/fixture-mapping-notes.yaml with
AgentDojo's "direct" attack and the scripted test double from the fixture suite, which carries out
instructions it reads in tool results. No model and no network are involved. Needs AgentDojo:

    pip install -e ".[dojo]"
    python3 scripts/make_fixture_results.py

Call durations are zeroed and the server command is written relative to the repository, so the
file only changes when behaviour changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

from agentdojo_mcp.bridge import run_benchmark  # noqa: E402
from agentdojo_mcp.dojo import load_suite  # noqa: E402
from agentdojo_mcp.mapping import load as load_mapping  # noqa: E402
from agentdojo_mcp.mcp_client import connect  # noqa: E402
from agentdojo_mcp.results import dump  # noqa: E402

SERVER = "tests/fixtures/fixture-server.py"
MAPPING = FIXTURES / "fixture-mapping-notes.yaml"
SUITE = f"{FIXTURES / 'fixture-dojo-suite' / 'fixture-suite.py'}:suite"


def generate() -> dict:
    suite = load_suite(SUITE)
    double = sys.modules[suite.environment_type.__module__]
    mapping = load_mapping(MAPPING)
    with connect(stdio=[sys.executable, str(ROOT / SERVER)]) as client:
        data = run_benchmark(
            suite,
            mapping,
            client,
            double.FollowsToolText,
            "scripted-test-double",
            benchmark_version="v1.2.2",
            attack="direct",
            mapping_file=str(MAPPING),
        )
    data["server"]["target"] = f"python3 {SERVER}"
    for call in data["calls"]:
        call["ms"] = 0
    return data


def main() -> int:
    out = FIXTURES / "fixture-results.json"
    dump(generate(), out)
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
