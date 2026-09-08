"""Exploration tools: `list_columns`, `describe_column`, `filter_rows`.

These are what the agent reaches for first. Their job is to let the model find
out what it is looking at *before* it computes anything, which is what keeps it
from guessing column names.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from app.datasets.store import column_kind
from app.exceptions import ToolError
from app.serialization import frame_to_records, jsonify
from app.tools.registry import ToolContext, registry, require_column, require_non_empty

SelectionId = Annotated[
    str | None,
    Field(
        default=None,
        description="Id de sélection renvoyé par filter_rows, pour travailler sur un sous-ensemble.",
    ),
]


# ---------------------------------------------------------------------------
# list_columns
# ---------------------------------------------------------------------------


class ListColumnsArgs(BaseModel):
    """Arguments of `list_columns`."""

    selection_id: SelectionId = None


@registry.register(
    name="list_columns",
    description=(
        "Liste les colonnes du dataset avec, pour chacune : le type "
        "(numeric, categorical, datetime, boolean), le nombre de valeurs nulles, "
        "la cardinalité et quelques exemples de valeurs. "
        "À appeler en premier quand on ne connaît pas encore le dataset."
    ),
    args_model=ListColumnsArgs,
)
def list_columns(args: ListColumnsArgs, context: ToolContext) -> dict[str, Any]:
    """Describe every column: kind, nulls, cardinality, sample values."""
    frame = context.frame(args.selection_id)
    columns = [
        {
            "name": str(name),
            "kind": column_kind(frame[name]),
            "dtype": str(frame[name].dtype),
            "null_count": int(frame[name].isna().sum()),
            "cardinality": int(frame[name].nunique(dropna=True)),
            "sample_values": [jsonify(value) for value in frame[name].dropna().unique()[:3]],
        }
        for name in frame.columns
    ]
    return {
        "row_count": len(frame),
        "column_count": len(columns),
        "columns": columns[: context.max_rows],
        "truncated": len(columns) > context.max_rows,
    }


# ---------------------------------------------------------------------------
# describe_column
# ---------------------------------------------------------------------------


class DescribeColumnArgs(BaseModel):
    """Arguments of `describe_column`."""

    column: str = Field(description="Nom exact de la colonne à décrire.")
    selection_id: SelectionId = None


@registry.register(
    name="describe_column",
    description=(
        "Statistiques descriptives d'une colonne. Numérique : count, mean, std, min, "
        "quartiles, max, somme. Catégorielle : nombre de modalités et distribution des "
        "plus fréquentes. Date : étendue temporelle."
    ),
    args_model=DescribeColumnArgs,
)
def describe_column(args: DescribeColumnArgs, context: ToolContext) -> dict[str, Any]:
    """Return descriptive statistics adapted to the column's kind."""
    frame = require_non_empty(context.frame(args.selection_id))
    name = require_column(frame, args.column)
    series = frame[name]
    kind = column_kind(series)
    non_null = series.dropna()

    base: dict[str, Any] = {
        "column": name,
        "kind": kind,
        "count": len(non_null),
        "null_count": int(series.isna().sum()),
        "cardinality": int(series.nunique(dropna=True)),
    }

    if kind == "numeric" and not non_null.empty:
        described = non_null.describe()
        base["statistics"] = {
            "mean": jsonify(described["mean"]),
            "std": jsonify(described.get("std")),
            "min": jsonify(described["min"]),
            "p25": jsonify(described["25%"]),
            "median": jsonify(described["50%"]),
            "p75": jsonify(described["75%"]),
            "max": jsonify(described["max"]),
            "sum": jsonify(non_null.sum()),
        }
    elif kind == "datetime" and not non_null.empty:
        span = non_null.max() - non_null.min()
        base["statistics"] = {
            "min": jsonify(non_null.min()),
            "max": jsonify(non_null.max()),
            "span_days": int(span.days),
        }
    else:
        counts = non_null.value_counts().head(context.max_rows)
        total = len(non_null)
        base["top_values"] = [
            {
                "value": jsonify(value),
                "count": int(count),
                "share": round(float(count) / total, 6) if total else None,
            }
            for value, count in counts.items()
        ]
        base["truncated"] = int(series.nunique(dropna=True)) > context.max_rows

    return base


# ---------------------------------------------------------------------------
# filter_rows
# ---------------------------------------------------------------------------

ConditionOp = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null", "not_null"
]


class Condition(BaseModel):
    """One filter clause."""

    column: str
    op: ConditionOp
    value: Any = Field(
        default=None,
        description="Valeur comparée. Liste pour in/not_in, absente pour is_null/not_null.",
    )


class FilterRowsArgs(BaseModel):
    """Arguments of `filter_rows`."""

    conditions: list[Condition] = Field(min_length=1, max_length=8)
    combine: Literal["and", "or"] = "and"
    selection_id: SelectionId = None


@registry.register(
    name="filter_rows",
    description=(
        "Applique des conditions et renvoie un RÉSUMÉ (nombre de lignes retenues, part du "
        "total, aperçu) plus un selection_id réutilisable dans les autres outils pour "
        "analyser ce sous-ensemble sans réécrire le filtre."
    ),
    args_model=FilterRowsArgs,
)
def filter_rows(args: FilterRowsArgs, context: ToolContext) -> dict[str, Any]:
    """Filter rows, save the selection, and return a summary rather than the data."""
    frame = require_non_empty(context.frame(args.selection_id))

    masks = [_build_mask(frame, condition) for condition in args.conditions]
    mask = masks[0]
    for other in masks[1:]:
        mask = (mask & other) if args.combine == "and" else (mask | other)

    selected = frame.loc[mask]
    selection_id = context.store.save_selection(context.dataset_id, selected.index)
    total = len(frame)
    matched = len(selected)

    preview_rows = min(5, context.max_rows)
    return {
        "selection_id": selection_id,
        "matched_rows": matched,
        "total_rows": total,
        "share": round(matched / total, 6) if total else 0.0,
        "preview": frame_to_records(selected.head(preview_rows)),
        "note": (
            "Aucune ligne ne satisfait ces conditions."
            if matched == 0
            else f"Passez selection_id={selection_id!r} aux autres outils pour analyser ce sous-ensemble."
        ),
    }


def _build_mask(frame: pd.DataFrame, condition: Condition) -> pd.Series:
    """Translate one condition into a boolean mask, or raise an explicit ToolError."""
    name = require_column(frame, condition.column)
    series = frame[name]
    op = condition.op
    value = condition.value

    if op == "is_null":
        return series.isna()
    if op == "not_null":
        return series.notna()

    if op in {"in", "not_in"}:
        if not isinstance(value, list | tuple):
            raise ToolError(
                f"L'opérateur « {op} » attend une liste de valeurs.",
                detail=f"Reçu : {type(value).__name__}.",
            )
        member = series.isin(list(value))
        return member if op == "in" else ~member

    if value is None:
        raise ToolError(
            f"L'opérateur « {op} » nécessite une valeur de comparaison.",
            detail="Utilisez is_null / not_null pour tester l'absence de valeur.",
        )

    if op == "contains":
        return series.astype("string").str.contains(str(value), case=False, na=False)

    comparison_value = _coerce_for_comparison(series, value, name, op)
    operators = {
        "eq": series.eq,
        "ne": series.ne,
        "gt": series.gt,
        "gte": series.ge,
        "lt": series.lt,
        "lte": series.le,
    }
    return operators[op](comparison_value).fillna(False)


def _coerce_for_comparison(series: pd.Series, value: Any, name: str, op: str) -> Any:
    """Coerce a JSON value to the column's type, refusing impossible comparisons."""
    kind = column_kind(series)
    if kind == "datetime":
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            raise ToolError(
                f"« {value} » n'est pas une date valide pour la colonne « {name} ».",
                detail="Utilisez le format ISO, par exemple 2024-03-01.",
            )
        return parsed
    if kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ToolError(
                f"La colonne « {name} » est numérique : « {value} » ne peut pas être comparé.",
            )
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ToolError(
                f"La colonne « {name} » est numérique : « {value} » n'est pas un nombre.",
            ) from exc
    if kind == "categorical" and op in {"gt", "gte", "lt", "lte"}:
        raise ToolError(
            f"La colonne « {name} » est catégorielle : les comparaisons d'ordre n'ont pas de sens.",
            detail="Utilisez eq, ne, in, not_in ou contains.",
        )
    return value


__all__ = ["describe_column", "filter_rows", "list_columns"]
