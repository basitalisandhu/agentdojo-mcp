# Good first issues

Issues the maintainer intends to open under the `good first issue` label, written out so they can be filed in one sitting. Each is self-contained and has acceptance criteria that `make check` can verify. Read [CONTRIBUTING.md](../CONTRIBUTING.md) first: `ruff` must pass, the core stays standard-library only, tests run offline, and no attack text is committed.

## 1. `replay --diff` between two results files

**Context.** A regression gate wants to know which tasks changed between two runs of the same mapping, not only the totals.

**Acceptance criteria.**

- `replay new.json --diff old.json` prints the user tasks and pairs whose utility or attack success changed, and the change in each headline rate.
- Exit code 1 when attack success went up or utility went down, 0 otherwise.
- Tests build two small results documents in `tmp_path`.

## 2. Description similarity in `map`

**Context.** Names alone miss pairs like `get_most_recent_transactions` and `list_transactions` (see `tests/fixtures/fixture-suite-ledger.json`).

**Acceptance criteria.**

- Tool descriptions contribute a token-overlap term to the score, documented in `docs/mapping.md`.
- The ledger fixture proposes `list_transactions` for `get_most_recent_transactions`; no existing proposal test changes.

## 3. `inspect --format markdown`

**Context.** People paste a server's tool list into issues and pull requests.

**Acceptance criteria.**

- A Markdown table with tool name, required and optional parameters with types, and the first line of the description.
- Tests run against the stdio fixture server.

## 4. Per-call timing in the Markdown report

**Context.** `results.json` records `ms` for every call, but no report shows it.

**Acceptance criteria.**

- The Markdown calls table gains a duration column, and the Tools table a total per tool.
- The text report stays unchanged, so `docs/demo.svg` does not change.

## 5. Mapping for a public MCP server

**Context.** Worked mappings make the tool easier to try.

**Acceptance criteria.**

- A mapping under `docs/mappings/` for one built-in suite and one public MCP server, with the server version in a comment and the `validate-mapping` output in the pull request.
- No credentials in the file; any account ids go in `constants` as placeholders.

## 6. `--tasks` glob patterns

**Context.** Selecting ten tasks takes ten flags.

**Acceptance criteria.**

- `--tasks 'user_task_1*'` and `--injection-task 'injection_task_[0-3]'` select by `fnmatch` pattern; a pattern that matches nothing is an error naming it.
- Tests cover the selection function without AgentDojo installed.
