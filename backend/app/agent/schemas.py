"""Tool schemas as sent to the LLM.

Thin on purpose: the schemas are *derived* from the Pydantic argument models in
`app.tools`, never written twice. A tool whose arguments change cannot drift out
of sync with what the model is told, which is the classic way tool-calling
agents break in production.
"""

from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry
from app.tools.registry import registry as default_registry


def tool_schemas(registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Return `[{name, description, input_schema}, ...]` for every registered tool."""
    return (registry or default_registry).schemas()


def tool_names(registry: ToolRegistry | None = None) -> list[str]:
    """Return the registered tool names, sorted."""
    return (registry or default_registry).names()
