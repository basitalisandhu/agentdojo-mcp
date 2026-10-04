# Mapping files

A mapping tells agentdojo-mcp which MCP server tool stands in for each tool of an AgentDojo task suite, how to rename arguments on the way, and how to treat the result. `agentdojo-mcp map` writes a first draft; you edit it; `agentdojo-mcp validate-mapping` checks it; `agentdojo-mcp run` uses it.

Mappings are YAML (a small subset: block mappings and lists, comments, plain and quoted scalars, JSON for inline values) or JSON. Anchors, tags and multi-line strings are not supported; write JSON if you need something the subset does not cover.

## Example

```yaml
version: 1
suite: notes
benchmark_version: v1.2.2
seed:
  tool: load_state
  argument: state
tools:
  add_note:
    server_tool: notes_create
    mode: both
    arguments:
      title: name
      body: text
    constants: {}
    drop: []
    result: auto
  read_note:
    server_tool: notes_get
    mode: server
    arguments:
      title: name
    constants: {}
    drop: []
    result: auto
  search_notes:
    server_tool: null
    mode: local
    arguments: {}
    constants: {}
    drop: []
    result: auto
```

This is [tests/fixtures/fixture-mapping-notes.yaml](../tests/fixtures/fixture-mapping-notes.yaml) without its comments.

## Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | Always `1`. |
| `suite` | yes | The AgentDojo suite name. `run` refuses a mapping whose suite differs from `--suite`. |
| `benchmark_version` | no | The AgentDojo benchmark version the mapping was written for; `run` uses it when `--benchmark-version` is not given. |
| `seed` | no | A server tool that loads the suite environment; see below. |
| `tools` | yes | One entry per suite tool. Suite tools left out run locally, and `validate-mapping` warns about them. |

## Tool entries

| Key | Default | Meaning |
|---|---|---|
| `server_tool` | `null` | The server tool to call. Required unless `mode` is `local`. |
| `mode` | `server` when `server_tool` is set, else `local` | How the call is made; see modes. |
| `arguments` | `{}` | Suite parameter name to server parameter name. Parameters not listed keep their name. |
| `constants` | `{}` | Extra server arguments with fixed values, for required server parameters the suite tool does not have (a currency, an account id). `map` writes `null` for each one it could not fill, and `validate-mapping` reports a `null` for a required parameter as an error. |
| `drop` | `[]` | Suite parameters the server does not take. Dropping a required suite parameter is a warning. |
| `result` | `auto` | How the server's answer becomes the tool's return value; see results. |

## Modes

The agent always sees the suite's tool names, descriptions and parameters, so AgentDojo's prompts, ground truths and checks work unchanged. The mode decides what happens when the agent calls a tool.

- `server`: the bridge translates the arguments and calls `server_tool`; the server's answer is the tool result. AgentDojo's environment is not touched. Right for tools that only read.
- `both`: the bridge first runs AgentDojo's own implementation, which updates AgentDojo's environment the way the suite expects, then calls the server; the agent sees the server's answer. Right for tools that change state, because AgentDojo's utility and attack checks read that environment after the task. The server must accept the call; if it returns an error the agent sees the error, but AgentDojo's environment has already changed.
- `local`: AgentDojo's implementation only, nothing is sent to the server. For suite tools the server has no counterpart for. Reports list them so it is clear which part of the run did not touch the server.

`map` proposes `both` for tools that changed the environment when AgentDojo replayed the suite's ground truths (the `mutates` flag in a suite dump), and `server` for the rest. `validate-mapping` warns when such a tool is in `server` mode.

## Seeding

Before each task (each user task, each injection task run on its own, and each pair) AgentDojo builds the environment, with the attack's text placed into its injection vectors. Server-mode tools read the server's data, not that environment, so without help the injected text never reaches the agent through them.

`seed` names a server tool that accepts the environment: before every task the bridge calls it with one argument, named by `argument` (default `state`), whose value is AgentDojo's environment as JSON (`env.model_dump(mode="json")`). A server built for benchmarking can load it and answer from it; [tests/fixtures/fixture-server.py](../tests/fixtures/fixture-server.py) shows the pattern for a small suite. The call log records the seed call with the size of the environment, not its content, so results files never hold injected text.

Servers that cannot load state can still be benchmarked: use `both` mode for every tool whose result carries injectable content, so that the agent sees the server's answer and AgentDojo keeps the injected data; or point the server at data you prepared with the injection placeholders filled. `validate-mapping` warns when server-mode tools exist and no seed is set.

## Results

- `auto`: the result's `structuredContent` when it is an object, otherwise the text blocks joined with newlines (non-text blocks appear as `[image content]` and similar).
- `text`: always the joined text blocks.
- `json`: the joined text parsed as JSON, or the text when it is not JSON. Use it when a server returns JSON as text and you want AgentDojo to format it like a Python value.

A result with `isError: true` raises an error inside the tool, which AgentDojo hands to the agent as a tool error. JSON-RPC errors do the same. A server that goes away ends the run.

## How proposals are scored

For every suite tool and every server tool, `map` computes:

- name similarity: names are split on `_`, `-`, `.` and camelCase, lowercased, plurals folded (`notes` to `note`), and common verbs and nouns folded to one word (`read`, `fetch` and `get`; `add` and `create`; `transfer` and `send`). The score blends token overlap with a character-level ratio. A reading verb against a writing verb (get against delete), or two different writing verbs, scales the score down; two different reading verbs (search against get) scale it down less.
- schema shape: suite parameters are paired with server parameters by the same name similarity, when their JSON Schema types are compatible. Required parameters left over on both sides pair up in order when they are equal in number (`title, body` to `name, text`); those positional pairs count half. Required server parameters nothing feeds lower the score.

The total is 0.65 times name similarity plus 0.35 times shape. The best server tool is proposed when the total is 0.45 or more; otherwise the tool stays `local`. Each entry carries a comment with its score and the next two candidates. A server tool named `load_state`, `seed`, `seed_state`, `reset_state`, `load_fixture` or `set_state` becomes the seed and is never proposed as a stand-in.

## Validation

`validate-mapping` reports errors (the run would fail or measure the wrong thing) and warnings (the run works but read the message). Errors: unknown suite or server tools (with a "did you mean"), a suite parameter mapped that the suite tool does not have, two suite parameters feeding one server parameter, a parameter both mapped and dropped, a server parameter the server tool does not have, a required server parameter nothing supplies or whose constant is `null`, a missing seed tool or seed argument, the seed tool used as a stand-in, and a suite name that differs from the suite dump. Warnings: suite tools missing from the mapping, a changing tool in `server` mode, type mismatches, an optional suite parameter feeding a required server parameter, dropped required suite parameters, and no seed while server-mode tools exist. `--strict` fails on warnings too. `run` validates against the live server before it starts unless you pass `--no-validate`.
