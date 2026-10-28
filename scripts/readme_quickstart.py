"""Print the README's quick start (its first ``bash`` code block) as a shell script.

The only change is the clone line: ``git clone <github url>`` becomes a clone of this checkout,
so CI tests the code under review rather than main. Everything else runs exactly as written.
Used by the ``readme-quickstart`` CI job and by ``tests/test_repo.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLONE_URL = "https://github.com/basitalisandhu/agentdojo-mcp"


def quickstart(readme: str) -> list[str]:
    match = re.search(r"^```bash\n(.*?)^```", readme, re.M | re.S)
    if not match:
        raise SystemExit("README.md has no bash code block")
    return [line for line in match.group(1).splitlines() if line.strip()]


def as_script(lines: list[str], checkout: Path = ROOT) -> str:
    out = [line.replace(f"git clone {CLONE_URL}", f"git clone {checkout}") for line in lines]
    return "\n".join(out) + "\n"


def main() -> int:
    sys.stdout.write(as_script(quickstart((ROOT / "README.md").read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
