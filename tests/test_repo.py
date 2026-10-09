"""Repository hygiene: no attack text is committed, docs match the code, house style holds."""

from __future__ import annotations

import re
from pathlib import Path

from agentdojo_mcp import __version__
from agentdojo_mcp.mapping import MODES, RESULT_MODES

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "dist", "build", ".pytest_cache", ".ruff_cache", "__pycache__"}
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".txt", ".svg", ".cfg", ""}

# Instruction-override phrasing of the kind prompt-injection payloads use. Assembled from parts so
# that this file does not contain the phrases it looks for. AgentDojo's attacks provide such text
# at run time; none of it belongs in this repository.
FORBIDDEN = [
    " ".join(words)
    for words in (
        ("ignore", "previous", "instructions"),
        ("ignore", "all", "previous"),
        ("ignore", "the", "above"),
        ("disregard", "previous"),
        ("disregard", "the", "above"),
        ("forget", "your", "instructions"),
        ("new", "instructions:"),
        ("system", "override"),
        ("before", "you", "can", "solve", "the", "task"),
        ("important", "instructions", "from", "me"),
        ("this", "is", "an", "important", "message"),
    )
]


def text_files() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)
        and p.suffix in TEXT_SUFFIXES
    ]


def test_no_instruction_override_phrases_are_committed():
    hits = []
    for path in text_files():
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8", errors="ignore").lower())
        hits += [f"{path.relative_to(ROOT)}: {phrase!r}" for phrase in FORBIDDEN if phrase in text]
    assert hits == []


def test_forbidden_list_is_not_empty_and_lowercase():
    assert len(FORBIDDEN) >= 10 and all(p == p.lower() for p in FORBIDDEN)


EM_DASH = chr(0x2014)


def test_no_em_dashes_anywhere():
    assert [
        str(p.relative_to(ROOT))
        for p in text_files()
        if EM_DASH in p.read_text(encoding="utf-8", errors="ignore")
    ] == []


def test_fixtures_are_named_fixture():
    fixtures = ROOT / "tests" / "fixtures"
    names = [
        p.relative_to(fixtures).parts[0]
        for p in fixtures.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    assert names and all(n.startswith("fixture-") for n in names)


def test_readme_leads_with_the_search_phrase():
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# ") and "Run AgentDojo against MCP servers" in lines[0]
    assert lines[2].startswith("**agentdojo-mcp maps an AgentDojo task suite")


def test_version_is_consistent():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(__version__)}"$', pyproject, re.M)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M)


def test_mapping_doc_covers_every_mode_and_result_mode():
    doc = (ROOT / "docs" / "mapping.md").read_text(encoding="utf-8")
    for word in (*MODES, *RESULT_MODES, "seed", "constants", "drop", "arguments"):
        assert f"`{word}`" in doc, word


def test_good_first_issues_has_six_tasks():
    doc = (ROOT / "docs" / "good-first-issues.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## \d+\. ", doc, re.M)) == 6


def test_readme_cites_agentdojo_issues_by_url():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for number in (194, 201, 209, 212, 213, 214):
        assert f"https://github.com/ethz-spylab/agentdojo/issues/{number}" in readme or (
            f"https://github.com/ethz-spylab/agentdojo/pull/{number}" in readme
        ), number
