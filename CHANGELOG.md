# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-10-28

### Added

- `replay NEW --diff OLD` compares two results files: each headline rate before and after, every user task, injection task and pair whose utility or attack success changed, and the tasks found in only one file. It exits 1 when targeted attack success went up or utility (without or under attack) went down, so it works as a regression gate; `--format markdown` and `--format json` work too (#1).
- README: a Troubleshooting section, and a CI job that follows the quick start exactly on a fresh virtual environment.
- Fuzz and property tests (Hypothesis) for the mapping reader, server and suite dumps, results files, messages from a stdio server and command-line values, and scaling tests that fail on quadratic work.

### Fixed

- A stdio server that printed a deeply nested JSON line, or bytes that are not UTF-8, stopped the client's reader thread, so every request then waited for the full `--timeout` and failed with a misleading message; such lines are now skipped like other non-protocol output, and a reader that stops always wakes the waiting request.
- An `initialize` result whose `serverInfo` was not an object, or a tool without a string name in `tools/list`, raised `AttributeError` or `KeyError`; the first is read as unnamed, the second is skipped, and a `nextCursor` that is not a string is an error.
- Mapping files that were not UTF-8 or nested too deeply, server dumps and results files nested too deeply, and a tool schema whose `required` was not a list ended in a traceback; each is now a one-line error with exit code 2 (or, for `required`, read as no required parameters).
- `replay` crashed on results files with a task without a string `id`, a non-object `mapping`, `server` or `mapping.tools` entry, a non-numeric `result_bytes` or an unusual `seq`; the file is now checked up front with a message naming the key, and odd values render as they are.
- `--timeout` accepted zero, negative values, `nan` and `inf` (the last crashed); it now needs a positive number of seconds. `--env` rejects names that are not variable names, `--header` rejects invalid header names and line breaks in values, and a `--stdio` command with an unclosed quote or a NUL byte is a clear error.

### Changed

- `run --tasks` and `--injection-task` look plain ids up directly; selecting many tasks by id was quadratic in the number of ids. `map` caches name tokenisation and similarity scores, which speeds up proposals for large tool sets.

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

[Unreleased]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/basitalisandhu/agentdojo-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/basitalisandhu/agentdojo-mcp/releases/tag/v0.1.0
