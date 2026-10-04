# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability") rather than a public issue. Include the version, the command you ran, and a minimal mapping, server dump or fixture server that reproduces the problem.

You will get an acknowledgement within 7 days and a fix or a mitigation plan within 30 days for confirmed issues. Credit is given in the release notes unless you prefer otherwise.

## Scope

agentdojo-mcp starts the MCP server you name (`--stdio`) or connects to the URL you give (`--url`), sends it `initialize`, `tools/list` and the `tools/call` requests that the agent or the ground truth makes, and writes the results and report files you ask for. With `run` it imports AgentDojo, which calls the model provider you choose.

Issues of interest:

- a stdio server receiving environment variables it was not given with `-e` (model provider keys in particular);
- a header value, `-e` value or API key appearing in a results file, a report or the call log;
- injected text from AgentDojo's attacks appearing in `results.json` or `report.md` through the seed call (the log records its size only);
- a mapping, server dump or results file that makes the tool write outside the paths you name, or crash or hang while parsing;
- plain-HTTP connections to a non-local host without `--allow-http`;
- a bridged call reaching a server tool other than the one the mapping names.

Out of scope: what the server does with the calls it receives (you choose the server), and what the model does with injected text (that is what the benchmark measures). Run untrusted servers in a container or a sandbox; `inspect` and `run` execute the `--stdio` command you give them.

This repository must not contain prompt-injection payloads. A test (`tests/test_repo.py`) fails on instruction-override phrasing anywhere in the tree; attacks come from the installed AgentDojo at run time.
