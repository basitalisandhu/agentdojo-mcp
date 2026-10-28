#!/usr/bin/env bash
# Run the README quick start against a fresh install in a temporary directory.
#
# Usage: scripts/e2e_quickstart.sh [path/to/agentdojo_mcp-*.whl]
# Without an argument the wheel is built first. The quick start uses the bundled fixture suite,
# fixture server and the ground-truth pipeline, so no model, key or outside server is used.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
python="${PYTHON:-python3}"
wheel="${1:-}"
if [ -z "$wheel" ]; then
  rm -rf "$repo/dist"
  "$python" -m pip wheel --quiet --no-deps -w "$repo/dist" "$repo"
  wheel="$(ls "$repo"/dist/agentdojo_mcp-*.whl)"
fi
wheel="$(cd "$(dirname "$wheel")" && pwd)/$(basename "$wheel")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
"$python" -m venv "$work/.venv"
# shellcheck disable=SC1091
. "$work/.venv/bin/activate"
pip install --quiet --disable-pip-version-check "${wheel}[dojo]"
# The quick start runs from a clone; only the fixtures are needed from it.
mkdir -p "$work/tests"
cp -R "$repo/tests/fixtures" "$work/tests/fixtures"
cd "$work"

step() { printf '\n$ %s\n' "$*"; }

step agentdojo-mcp run --suite tests/fixtures/fixture-dojo-suite/fixture-suite.py:suite ...
agentdojo-mcp run --suite tests/fixtures/fixture-dojo-suite/fixture-suite.py:suite \
  --mapping tests/fixtures/fixture-mapping-notes.yaml \
  --stdio "python3 tests/fixtures/fixture-server.py" --model ground-truth --attack direct | tee run.txt
grep -q "Utility, no attack       3/3 (100.0%)" run.txt
test -s agentdojo-mcp-results/results.json
test -s agentdojo-mcp-results/report.md

step agentdojo-mcp replay agentdojo-mcp-results/results.json --format markdown
agentdojo-mcp replay agentdojo-mcp-results/results.json --format markdown | grep -q "Targeted attack success"

step agentdojo-mcp replay agentdojo-mcp-results/results.json --diff tests/fixtures/fixture-results.json
set +e
agentdojo-mcp replay agentdojo-mcp-results/results.json --diff tests/fixtures/fixture-results.json
rc=$?
set -e
# The fixture results came from a scripted model that followed injections; ground truth does not,
# so attack success can only go down: no regression, exit 0.
test "$rc" -eq 0

printf '\nquick start OK (%s)\n' "$(agentdojo-mcp --version)"
