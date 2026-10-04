"""docs/demo.svg regenerates from the committed fixture results with scripts/render_demo.py."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_demo.py"
SVG_NS = "{http://www.w3.org/2000/svg}"
COMMAND = "agentdojo-mcp replay tests/fixtures/fixture-results.json"
EXPECTED = "Targeted attack success  2/3 (66.7%)"


def svg_text(path: Path) -> str:
    root = ET.parse(path).getroot()
    assert root.tag == f"{SVG_NS}svg"
    return "\n".join("".join(t.itertext()) for t in root.iter(f"{SVG_NS}text")).replace(
        chr(0xA0), " "
    )


def test_render_demo_regenerates_a_well_formed_svg(tmp_path: Path) -> None:
    out = tmp_path / "demo.svg"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    text = svg_text(out)
    assert f"$ {COMMAND}" in text and EXPECTED in text
    assert str(ROOT) not in text


def test_committed_demo_matches_a_fresh_render(tmp_path: Path) -> None:
    out = tmp_path / "demo.svg"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(out)], cwd=ROOT, check=True, timeout=300
    )
    assert out.read_text(encoding="utf-8") == (ROOT / "docs" / "demo.svg").read_text(
        encoding="utf-8"
    )
