# Contributing

Thanks for considering a contribution. The project is small on purpose: a JSON-RPC client for MCP, a mapping format with a proposer and a validator, a bridge that swaps an AgentDojo suite's tools for MCP calls, and a report renderer. The most useful contributions are mapping proposals that go wrong (with the two dumps that show it), transport problems with a minimal server, and keeping the bridge in step with AgentDojo releases.

## Set up

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/basitalisandhu/agentdojo-mcp
cd agentdojo-mcp
uv venv && uv pip install -e ".[dev]"          # core: tests marked dojo are skipped
uv pip install -e ".[dev,dojo]"                # with AgentDojo: every test runs
uv run pytest -q
```

Without uv:

```bash
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e ".[dev,dojo]"
python3 -m pytest -q
```

## Before you open a pull request

```bash
make check      # ruff check, ruff format --check, pytest
make demo       # validate the fixture mapping and replay the fixture results
```

CI runs the core tests on Python 3.11 and 3.12, the AgentDojo integration tests in a separate job, and builds the container image.

## Where things live

- `src/agentdojo_mcp/mcp_client.py`: stdio and Streamable HTTP transports, the handshake, `tools/list` pagination, `tools/call`.
- `src/agentdojo_mcp/yamlish.py`: the YAML subset mapping files use.
- `src/agentdojo_mcp/schema.py`: suite dumps and server dumps reduced to tool specs.
- `src/agentdojo_mcp/mapping.py`: the mapping format, proposals and validation. Changes to keys or scoring need a matching change in `docs/mapping.md`; `tests/test_repo.py` checks the key names.
- `src/agentdojo_mcp/bridge.py` and `dojo.py`: everything that imports AgentDojo. Keep AgentDojo imports inside functions so the core runs without it.
- `src/agentdojo_mcp/results.py` and `report.py`: the results format and its renderers.

## Tests and fixtures

Everything runs offline. Fixtures live in `tests/fixtures/` and their names start with `fixture-`: a stdio MCP server, an AgentDojo suite of three tasks with its data, suite and server dumps, a mapping, and a results file. `tests/fixtures/fixture-results.json` is produced by `scripts/make_fixture_results.py`; regenerate it and `docs/demo.svg` (`python3 scripts/render_demo.py`) when behaviour changes, and commit both. Tests that need AgentDojo carry `@pytest.mark.dojo` and are skipped when it is not installed.

Do not commit attack text. Injection payloads come from AgentDojo at run time; fixtures use plain task goals. `tests/test_repo.py` fails on instruction-override phrasing anywhere in the repository.

## Style

- `ruff` formats and lints; line length 100.
- The core stays standard library only. AgentDojo is the only optional dependency.
- No model names in code, docs or fixtures; use `provider:model-name` as a placeholder.
- Plain language, no em dashes, and no numbers that a test or a command in this repository does not produce.

## Reporting security issues

See [SECURITY.md](SECURITY.md).
