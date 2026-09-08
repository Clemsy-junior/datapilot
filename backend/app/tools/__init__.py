"""Deterministic tool sandbox.

Importing this package is what populates the registry: each module registers its
tools at import time, so `app.tools.registry.registry` is complete as soon as
`app.tools` has been imported once.
"""

from app.tools import analysis, charts, exploration  # noqa: F401  (import for side effects)
from app.tools.registry import ToolContext, ToolRegistry, registry

__all__ = ["ToolContext", "ToolRegistry", "registry"]
