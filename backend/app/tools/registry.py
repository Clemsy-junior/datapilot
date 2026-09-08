"""Tool registry: registration, argument validation and dispatch.

This module is the sandbox boundary. The LLM never touches pandas: it emits a
tool name and a JSON object, and everything past this point is deterministic
Python that either produces a bounded, JSON-safe result or raises `ToolError`
with a message written to be *actionable by the model itself*.

Three invariants hold for every registered tool and are enforced here rather
than in each tool:

1. arguments are validated by a Pydantic model before the handler runs;
2. results are bounded (`MAX_TOOL_ROWS`) so a wide join can never blow up the
   context window or the bill;
3. failures are values, not crashes — a bad column name comes back as a message
   the model can read and correct.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import pandas as pd
from pydantic import BaseModel, ValidationError

from app.datasets.store import DatasetStore, column_kind
from app.exceptions import DataPilotError, ToolError

#: Aggregation functions exposed to the agent. Deliberately short: every one of
#: them is unambiguous on both numeric and grouped data.
AggFunc = Literal["sum", "mean", "count", "min", "max", "median"]

ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass
class ToolContext:
    """Everything a tool is allowed to reach.

    Passing this explicitly (instead of importing a global store) is what makes
    the tools unit-testable without an HTTP server, an event loop or an LLM.
    """

    store: DatasetStore
    dataset_id: str
    max_rows: int = 50
    #: Results of the tools already run during this agent turn, keyed by
    #: `result_id`. `make_chart` reads its rows from here, which is how the
    #: project guarantees a chart cannot contain a number the model invented.
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0

    def frame(self, selection_id: str | None = None) -> pd.DataFrame:
        """Return the working DataFrame, restricted to a saved selection if asked."""
        frame = self.store.frame(self.dataset_id)
        if selection_id is None:
            return frame
        index = self.store.get_selection(self.dataset_id, selection_id)
        if index is None:
            raise ToolError(
                f"La sélection « {selection_id} » n'existe pas.",
                detail="Relancez filter_rows pour en créer une nouvelle.",
            )
        return frame.loc[index]

    def next_result_id(self) -> str:
        """Return a short, stable id for the next tool result of this turn."""
        self._counter += 1
        return f"res_{self._counter}"


@dataclass(frozen=True)
class Tool:
    """A registered tool: its schema for the LLM and its Python implementation."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any, ToolContext], dict[str, Any]]

    def json_schema(self) -> dict[str, Any]:
        """Return the JSON Schema of the arguments, as sent to the LLM."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return schema


class ToolRegistry:
    """Name -> tool mapping, plus validated dispatch."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self, name: str, description: str, args_model: type[BaseModel]
    ) -> Callable[[Callable[[Any, ToolContext], dict[str, Any]]], Callable[..., dict[str, Any]]]:
        """Decorator registering a handler under `name`."""

        def decorator(
            handler: Callable[[Any, ToolContext], dict[str, Any]],
        ) -> Callable[..., dict[str, Any]]:
            if name in self._tools:
                raise ValueError(f"tool already registered: {name}")
            self._tools[name] = Tool(
                name=name, description=description, args_model=args_model, handler=handler
            )
            return handler

        return decorator

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        """Return the registered tool names, sorted."""
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        """Return a tool by name, or raise `ToolError` listing what exists."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                f"Outil inconnu : « {name} ».",
                detail=f"Outils disponibles : {', '.join(self.names())}.",
            )
        return tool

    def schemas(self) -> list[dict[str, Any]]:
        """Return the LLM-facing description of every tool."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.json_schema(),
            }
            for tool in (self._tools[name] for name in self.names())
        ]

    def dispatch(
        self, name: str, raw_arguments: dict[str, Any], context: ToolContext
    ) -> tuple[dict[str, Any], float]:
        """Validate arguments, run the tool, and return `(result, duration_ms)`.

        Raises `ToolError` — never a bare pandas or Pydantic exception — so the
        agent loop has exactly one failure type to hand back to the model.
        """
        tool = self.get(name)
        try:
            arguments = tool.args_model.model_validate(raw_arguments or {})
        except ValidationError as exc:
            raise ToolError(
                f"Arguments invalides pour « {name} ».",
                detail=_format_validation_error(exc),
            ) from exc

        # TODO(R02): identical (tool, arguments) pairs are recomputed every
        # time — an LRU cache keyed on the normalised arguments goes here.
        started = time.perf_counter()
        try:
            result = tool.handler(arguments, context)
        except ToolError:
            raise
        except DataPilotError as exc:
            raise ToolError(exc.message, detail=exc.detail) from exc
        except (
            KeyError,
            IndexError,
            ValueError,
            TypeError,
            AttributeError,
            ArithmeticError,
        ) as exc:
            # Defence in depth: a tool bug becomes a recoverable tool error
            # rather than a 500 that kills the user's conversation.
            raise ToolError(
                f"L'outil « {name} » n'a pas pu traiter cette demande.",
                detail=str(exc)[:300],
            ) from exc
        duration_ms = (time.perf_counter() - started) * 1000

        result_id = context.next_result_id()
        result = {"result_id": result_id, **result}
        context.results[result_id] = result
        return result, duration_ms


def _format_validation_error(exc: ValidationError) -> str:
    """Render a Pydantic error compactly enough for a model to act on it."""
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(item) for item in error["loc"]) or "(racine)"
        parts.append(f"{location}: {error['msg']}")
    return " ; ".join(parts)


# ---------------------------------------------------------------------------
# Shared guards. Every tool validates through these so error wording — the part
# the model actually reads — stays identical across the whole tool surface.
# ---------------------------------------------------------------------------


def require_column(frame: pd.DataFrame, name: str) -> str:
    """Return `name` if it is a column of `frame`, else raise a helpful ToolError."""
    if name in frame.columns:
        return name
    available = [str(column) for column in frame.columns]
    suggestion = _closest(name, available)
    detail = f"Colonnes disponibles : {', '.join(available[:40])}."
    if suggestion:
        detail = f"Vouliez-vous dire « {suggestion} » ? {detail}"
    raise ToolError(f"La colonne « {name} » n'existe pas dans ce dataset.", detail=detail)


def require_numeric(frame: pd.DataFrame, name: str) -> str:
    """Return `name` if it is a numeric column, else raise a ToolError."""
    require_column(frame, name)
    if column_kind(frame[name]) != "numeric":
        raise ToolError(
            f"La colonne « {name} » n'est pas numérique, ce calcul ne s'y applique pas.",
            detail=f"Type détecté : {column_kind(frame[name])} ({frame[name].dtype}).",
        )
    return name


def require_datetime(frame: pd.DataFrame, name: str) -> str:
    """Return `name` if it is a datetime column, else raise a ToolError."""
    require_column(frame, name)
    if column_kind(frame[name]) != "datetime":
        raise ToolError(
            f"La colonne « {name} » n'est pas une colonne de dates.",
            detail=f"Type détecté : {column_kind(frame[name])} ({frame[name].dtype}).",
        )
    return name


def require_non_empty(frame: pd.DataFrame) -> pd.DataFrame:
    """Return `frame` if it has rows, else raise a ToolError."""
    if frame.empty:
        raise ToolError(
            "Aucune ligne à analyser.",
            detail="Le dataset ou la sélection en cours est vide ; élargissez le filtre.",
        )
    return frame


def bound_rows(frame: pd.DataFrame, max_rows: int) -> dict[str, Any]:
    """Return the first `max_rows` rows of `frame` as a bounded result fragment.

    Truncation is *reported*, never silent: the model is told the real total so
    it can say "top 50 of 812 regions" instead of implying it saw everything.
    """
    from app.serialization import frame_to_records

    total = len(frame)
    head = frame.head(max_rows)
    return {
        "rows": frame_to_records(head),
        "row_count": len(head),
        "total_rows": total,
        "truncated": total > max_rows,
    }


def _closest(name: str, candidates: list[str]) -> str | None:
    """Cheap fuzzy match used only to improve error messages."""
    import difflib

    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.7)
    return matches[0] if matches else None


#: The single registry instance. Tool modules import it and register on import;
#: `app.tools.__init__` imports those modules so the registry is complete.
registry = ToolRegistry()
