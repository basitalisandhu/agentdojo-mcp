.PHONY: install install-dojo lint format test check build demo fixtures clean

PY ?= uv run
FIX = tests/fixtures

install:
	uv venv
	uv pip install -e ".[dev]"

install-dojo:
	uv pip install -e ".[dev,dojo]"

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .

format:
	$(PY) ruff format .
	$(PY) ruff check --fix .

test:
	$(PY) pytest -q

check: lint test

build:
	rm -rf dist
	uv build

# Validate the fixture mapping, inspect the fixture server and replay the fixture results.
demo:
	$(PY) agentdojo-mcp validate-mapping $(FIX)/fixture-mapping-notes.yaml --suite $(FIX)/fixture-suite-notes.json --server-dump $(FIX)/fixture-server-notes.json
	$(PY) agentdojo-mcp inspect --stdio "python3 $(FIX)/fixture-server.py"
	$(PY) agentdojo-mcp replay $(FIX)/fixture-results.json

# Regenerate the fixture results and the README demo (needs the dojo extra).
fixtures:
	$(PY) python scripts/make_fixture_results.py
	$(PY) python scripts/render_demo.py

clean:
	rm -rf dist build .pytest_cache .ruff_cache agentdojo-mcp-results
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
