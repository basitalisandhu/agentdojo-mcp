# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-20

### Added

- `map` also reads tool descriptions: the first sentence of each description, read as a name, is compared with the other tool's name, and strong evidence (0.6 or more) can lift the name score. The ledger fixture now proposes `list_transactions` for `get_most_recent_transactions`, while a single shared word (`get_iban` against `account_balance`) still stays local (#2).

### Changed

- README first screen: who it is for, a three-line quick start that runs the bundled fixture suite end to end with no model or keys, and the start of the real report it prints. The `inspect` step in the quickstart drops `--json`, which printed the whole dump as well as writing it.

### Tests

- New `tests/test_dojo_load.py` covers suite loading errors (missing file, attribute that is not a `TaskSuite`, module that cannot be imported, unknown suite, unknown benchmark version), the module form, the missing-AgentDojo message and the version fallback.

## [0.2.0] - 2026-10-09

### Added

- `run --tasks` and `--injection-task` accept case-sensitive glob patterns; a pattern that matches nothing is an error naming it.

## [0.1.0] - 2026-10-04

### Added

- `agentdojo-mcp inspect`: initialize and tools/list against a stdio or Streamable HTTP MCP server with a standard-library JSON-RPC client (pagination, SSE responses, session ids, server requests declined); prints the tools or writes a server dump. Stdio servers get only a minimal environment plus `-e` variables.
- `agentdojo-mcp map`: proposes a mapping from a suite's tools to the server's tools by name similarity and schema shape, with `both` mode for tools that change suite state and a detected seed tool; writes commented YAML.
- `agentdojo-mcp validate-mapping`: errors and warnings for names, parameters, required arguments, types, modes and the seed tool; `--strict` and `--json`.
- `agentdojo-mcp run`: builds an AgentDojo suite whose tools call the server through the mapping (`server`, `both` and `local` modes, optional per-task seeding of the injected environment), runs user tasks, injection tasks and their pairs with AgentDojo's own benchmark functions, and writes `results.json`, `report.md` and AgentDojo's traces. `--model ground-truth` runs the reference solutions without a model; `provider:model-name` uses any provider AgentDojo supports.
- `agentdojo-mcp replay`: text, Markdown and JSON reports from a results file, offline.
- `agentdojo-mcp suites` and `dump-suite`: the bridging profile of AgentDojo's built-in suites, and suite dumps for offline mapping.
- Fixture MCP server, fixture AgentDojo suite and fixture results; CI on Python 3.11 and 3.12 with a separate AgentDojo job; container image `ghcr.io/basitalisandhu/agentdojo-mcp` published on version tags with an SPDX SBOM, a build provenance attestation and a keyless cosign signature; PyPI trusted publishing, off until the repository variable `PYPI_PUBLISH` is set.

[Unreleased]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/basitalisandhu/agentdojo-mcp/releases/tag/v0.1.0
