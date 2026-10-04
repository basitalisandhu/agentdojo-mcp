"""agentdojo-mcp: run AgentDojo task suites against the tools of a real MCP server.

The core (``inspect``, ``map``, ``validate-mapping``, ``replay``) uses only the Python standard
library. ``run``, ``suites`` and ``dump-suite`` import AgentDojo lazily; install it with
``pip install "agentdojo-mcp[dojo]"``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
