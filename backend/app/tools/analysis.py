"""Analysis tools: `aggregate`, `top_n`, `correlation`, `detect_outliers`, `time_series`.

Every number the user ever sees comes out of one of these functions. They are
plain, synchronous pandas: no LLM, no I/O, no global state, so each one can be
tested against a fixed DataFrame and a known expected value.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from app.exceptions import ToolError
from app.serialization import jsonify
from app.tools.registry import (
    AggFunc,
    ToolContext,
    bound_rows,
    registry,
    require_column,
    require_datetime,
    require_non_empty,
    require_numeric,
)

SelectionId = Annotated[
    str | None,
    Field(default=None, description="Id de sélection renvoyé par filter_rows."),
]

#: Aggregations that need no metric column, because they count rows.
_METRIC_FREE = {"count"}


def _apply_aggregation(grouped: Any, agg: AggFunc, metric: str | None) -> pd.Series:
    """Run one aggregation on a groupby, returning a Series named ``value``."""
    if agg == "count":
        series = grouped.size() if metric is None else grouped[metric].count()
    else:
        series = getattr(grouped[metric], agg)()
    return series.rename("value")


def _check_metric(frame: pd.DataFrame, agg: AggFunc, metric: str | None) -> str | None:
    """Validate the metric/aggregation pair, raising an actionable ToolError."""
    if agg in _METRIC_FREE:
        if metric is not None:
            require_column(frame, metric)
        return metric
    if metric is None:
        raise ToolError(
            f"L'agrégation « {agg} » nécessite une colonne metric numérique.",
            detail="Indiquez metric, ou utilisez agg='count' pour compter les lignes.",
        )
    return require_numeric(frame, metric)


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


class AggregateArgs(BaseModel):
    """Arguments of `aggregate`."""

    group_by: list[str] = Field(
        min_length=1, max_length=2, description="Une ou deux colonnes de regroupement."
    )
    agg: AggFunc = "sum"
    metric: str | None = Field(
        default=None, description="Colonne numérique agrégée. Facultative si agg='count'."
    )
    sort: Literal["value_desc", "value_asc", "group_asc"] = "value_desc"
    limit: int = Field(default=20, ge=1, le=200)
    selection_id: SelectionId = None


@registry.register(
    name="aggregate",
    description=(
        "Regroupe les lignes par une ou deux colonnes et applique une agrégation "
        "(sum, mean, count, min, max, median) sur une colonne numérique. "
        "Renvoie des lignes {colonnes de groupe..., value}."
    ),
    args_model=AggregateArgs,
)
def aggregate(args: AggregateArgs, context: ToolContext) -> dict[str, Any]:
    """Group and aggregate, returning bounded rows sorted as asked."""
    frame = require_non_empty(context.frame(args.selection_id))
    keys = [require_column(frame, name) for name in args.group_by]
    if len(set(keys)) != len(keys):
        raise ToolError("Les colonnes de regroupement doivent être distinctes.")
    metric = _check_metric(frame, args.agg, args.metric)

    grouped = frame.groupby(keys, dropna=False, observed=True)
    series = _apply_aggregation(grouped, args.agg, metric)
    result = series.reset_index()

    if args.sort == "group_asc":
        result = result.sort_values(keys)
    else:
        result = result.sort_values("value", ascending=args.sort == "value_asc")

    limit = min(args.limit, context.max_rows)
    payload = bound_rows(result.head(limit), limit)
    payload["total_rows"] = len(result)
    payload["truncated"] = len(result) > limit
    return {
        "group_by": keys,
        "agg": args.agg,
        "metric": metric,
        "value_label": f"{args.agg}({metric})" if metric else "count",
        **payload,
    }


# ---------------------------------------------------------------------------
# top_n
# ---------------------------------------------------------------------------


class TopNArgs(BaseModel):
    """Arguments of `top_n`."""

    metric: str = Field(description="Colonne numérique servant de classement.")
    n: int = Field(default=5, ge=1, le=100)
    direction: Literal["top", "bottom"] = "top"
    group_by: str | None = Field(
        default=None,
        description="Si fourni, agrège d'abord par cette colonne puis classe les groupes.",
    )
    agg: AggFunc = "sum"
    columns: list[str] | None = Field(
        default=None,
        max_length=8,
        description="Colonnes supplémentaires à renvoyer quand group_by est absent.",
    )
    selection_id: SelectionId = None


@registry.register(
    name="top_n",
    description=(
        "Renvoie les N premières ou dernières valeurs selon une métrique. "
        "Sans group_by : les N lignes extrêmes du dataset. "
        "Avec group_by : les N groupes extrêmes après agrégation."
    ),
    args_model=TopNArgs,
)
def top_n(args: TopNArgs, context: ToolContext) -> dict[str, Any]:
    """Rank rows or groups on a numeric metric."""
    frame = require_non_empty(context.frame(args.selection_id))
    ascending = args.direction == "bottom"
    limit = min(args.n, context.max_rows)

    if args.group_by is not None:
        key = require_column(frame, args.group_by)
        metric = _check_metric(frame, args.agg, args.metric)
        grouped = frame.groupby(key, dropna=False, observed=True)
        result = _apply_aggregation(grouped, args.agg, metric).reset_index()
        result = result.sort_values("value", ascending=ascending).head(limit)
        payload = bound_rows(result, limit)
        return {
            "mode": "groups",
            "group_by": key,
            "metric": metric,
            "agg": args.agg,
            "direction": args.direction,
            "value_label": f"{args.agg}({metric})" if metric else "count",
            **payload,
        }

    metric = require_numeric(frame, args.metric)
    extra = [require_column(frame, name) for name in (args.columns or [])]
    projection = list(dict.fromkeys([*extra, metric]))
    result = frame.dropna(subset=[metric]).sort_values(metric, ascending=ascending).head(limit)
    payload = bound_rows(result[projection], limit)
    return {
        "mode": "rows",
        "metric": metric,
        "direction": args.direction,
        **payload,
    }


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------


class CorrelationArgs(BaseModel):
    """Arguments of `correlation`."""

    column_a: str
    column_b: str
    selection_id: SelectionId = None


def _interpret(r: float) -> str:
    """Turn a coefficient into words, so the model does not have to invent a scale."""
    magnitude = abs(r)
    if magnitude < 0.1:
        strength = "négligeable"
    elif magnitude < 0.3:
        strength = "faible"
    elif magnitude < 0.5:
        strength = "modérée"
    elif magnitude < 0.7:
        strength = "notable"
    else:
        strength = "forte"
    direction = "positive" if r >= 0 else "négative"
    return f"corrélation {strength} {direction}"


@registry.register(
    name="correlation",
    description=(
        "Corrélation de Pearson entre deux colonnes numériques, calculée sur les lignes "
        "où les deux valeurs sont présentes. Renvoie le coefficient, l'effectif retenu "
        "et une interprétation qualitative."
    ),
    args_model=CorrelationArgs,
)
def correlation(args: CorrelationArgs, context: ToolContext) -> dict[str, Any]:
    """Compute Pearson's r between two numeric columns."""
    frame = require_non_empty(context.frame(args.selection_id))
    a = require_numeric(frame, args.column_a)
    b = require_numeric(frame, args.column_b)
    if a == b:
        raise ToolError("Corréler une colonne avec elle-même ne renseigne sur rien.")

    pair = frame[[a, b]].dropna()
    if len(pair) < 3:
        raise ToolError(
            "Trop peu de lignes avec les deux valeurs renseignées pour corréler.",
            detail=f"{len(pair)} ligne(s) exploitable(s), 3 au minimum.",
        )
    if pair[a].nunique() < 2 or pair[b].nunique() < 2:
        # Pearson divides by the standard deviations; a constant column makes
        # that a division by zero. Refuse explicitly instead of returning NaN.
        constant = a if pair[a].nunique() < 2 else b
        raise ToolError(
            f"La colonne « {constant} » est constante sur ces lignes : la corrélation n'est pas définie.",
            detail="Élargissez la sélection ou choisissez une autre colonne.",
        )

    coefficient = float(pair[a].corr(pair[b]))
    return {
        "column_a": a,
        "column_b": b,
        "n": len(pair),
        "pearson_r": round(coefficient, 6),
        "interpretation": _interpret(coefficient),
        "caveat": "Une corrélation n'établit pas de causalité.",
    }


# ---------------------------------------------------------------------------
# detect_outliers
# ---------------------------------------------------------------------------


class DetectOutliersArgs(BaseModel):
    """Arguments of `detect_outliers`."""

    column: str
    method: Literal["iqr", "zscore"] = "iqr"
    factor: float = Field(
        default=1.5,
        gt=0,
        le=10,
        description="Multiplicateur de l'IQR, ou seuil de z-score si method='zscore'.",
    )
    columns: list[str] | None = Field(
        default=None, max_length=8, description="Colonnes supplémentaires dans les exemples."
    )
    selection_id: SelectionId = None


@registry.register(
    name="detect_outliers",
    description=(
        "Détecte les valeurs aberrantes d'une colonne numérique. Méthode IQR par défaut "
        "(hors de [Q1 - k*IQR, Q3 + k*IQR]), ou z-score. Renvoie les bornes, le nombre "
        "de valeurs aberrantes et des exemples."
    ),
    args_model=DetectOutliersArgs,
)
def detect_outliers(args: DetectOutliersArgs, context: ToolContext) -> dict[str, Any]:
    """Flag outliers with IQR (default) or z-score."""
    frame = require_non_empty(context.frame(args.selection_id))
    column = require_numeric(frame, args.column)
    extra = [require_column(frame, name) for name in (args.columns or [])]

    values = frame[column].dropna()
    if len(values) < 4:
        raise ToolError(
            "Trop peu de valeurs pour estimer des valeurs aberrantes.",
            detail=f"{len(values)} valeur(s) non nulle(s), 4 au minimum.",
        )

    # Floating-point arithmetic rarely yields an exact zero spread even on a
    # constant column (0.2 repeated gives a std of ~1e-17), so "no spread" is a
    # tolerance relative to the magnitude of the data, not an equality test.
    scale = max(1.0, float(values.abs().max()))
    negligible = 1e-12 * scale

    if args.method == "iqr":
        q1, q3 = float(values.quantile(0.25)), float(values.quantile(0.75))
        iqr = q3 - q1
        if iqr <= negligible:
            raise ToolError(
                f"L'écart interquartile de « {column} » est nul : la méthode IQR ne s'applique pas.",
                detail="Essayez method='zscore' ou une autre colonne.",
            )
        lower, upper = q1 - args.factor * iqr, q3 + args.factor * iqr
        bounds = {"lower": round(lower, 6), "upper": round(upper, 6), "q1": q1, "q3": q3}
    else:
        mean, std = float(values.mean()), float(values.std(ddof=0))
        if std <= negligible:
            raise ToolError(
                f"L'écart-type de « {column} » est nul : le z-score n'est pas calculable.",
                detail="Toutes les valeurs sont identiques.",
            )
        lower, upper = mean - args.factor * std, mean + args.factor * std
        bounds = {"lower": round(lower, 6), "upper": round(upper, 6), "mean": mean, "std": std}

    mask = (frame[column] < lower) | (frame[column] > upper)
    outliers = frame.loc[mask.fillna(False)]
    ordered = outliers.assign(_deviation=(outliers[column] - values.median()).abs()).sort_values(
        "_deviation", ascending=False
    )
    projection = list(dict.fromkeys([*extra, column]))
    payload = bound_rows(ordered[projection], min(10, context.max_rows))

    total = len(values)
    return {
        "column": column,
        "method": args.method,
        "factor": args.factor,
        "bounds": {key: jsonify(value) for key, value in bounds.items()},
        "outlier_count": len(outliers),
        "share": round(len(outliers) / total, 6) if total else 0.0,
        "examples": payload["rows"],
        "examples_truncated": payload["truncated"],
    }


# ---------------------------------------------------------------------------
# time_series
# ---------------------------------------------------------------------------

#: pandas Period aliases. Weeks are anchored on Monday, months on the 1st, so
#: the buckets a user sees match the calendar they expect.
_PERIOD_FREQ = {"day": "D", "week": "W-MON", "month": "M"}

#: Cap on distinct series in a grouped time series, so a `group_by` on a
#: high-cardinality column cannot produce an unreadable chart or a huge payload.
_MAX_GROUPS = 6


class TimeSeriesArgs(BaseModel):
    """Arguments of `time_series`."""

    date_column: str = Field(description="Colonne de type date.")
    period: Literal["day", "week", "month"] = "month"
    agg: AggFunc = "sum"
    metric: str | None = Field(default=None, description="Facultative si agg='count'.")
    group_by: str | None = Field(
        default=None,
        description=f"Colonne catégorielle : produit une série par modalité ({_MAX_GROUPS} max).",
    )
    selection_id: SelectionId = None


@registry.register(
    name="time_series",
    description=(
        "Agrège une métrique par période (day, week, month) sur une colonne de dates. "
        "Avec group_by, renvoie une colonne par modalité (les plus importantes), prêt "
        "à être tracé en lignes multiples."
    ),
    args_model=TimeSeriesArgs,
)
def time_series(args: TimeSeriesArgs, context: ToolContext) -> dict[str, Any]:
    """Resample a metric over time, optionally split into a few series."""
    frame = require_non_empty(context.frame(args.selection_id))
    date_column = require_datetime(frame, args.date_column)
    metric = _check_metric(frame, args.agg, args.metric)

    working = frame.dropna(subset=[date_column]).copy()
    if working.empty:
        raise ToolError(
            f"La colonne « {date_column} » ne contient aucune date exploitable.",
        )
    working["period"] = working[date_column].dt.to_period(_PERIOD_FREQ[args.period])

    if args.group_by is None:
        grouped = working.groupby("period", dropna=False, observed=True)
        result = _apply_aggregation(grouped, args.agg, metric).reset_index()
        result["period"] = result["period"].astype(str)
        result = result.sort_values("period")
        payload = bound_rows(result, context.max_rows)
        return {
            "period": args.period,
            "date_column": date_column,
            "metric": metric,
            "agg": args.agg,
            "series_keys": ["value"],
            "value_label": f"{args.agg}({metric})" if metric else "count",
            **payload,
        }

    group_column = require_column(frame, args.group_by)
    grouped = working.groupby(["period", group_column], dropna=False, observed=True)
    long = _apply_aggregation(grouped, args.agg, metric).reset_index()
    totals = long.groupby(group_column, observed=True)["value"].sum().abs()
    kept = list(totals.sort_values(ascending=False).head(_MAX_GROUPS).index)
    long = long[long[group_column].isin(kept)]

    wide = long.pivot(index="period", columns=group_column, values="value")
    # Missing combinations stay NaN here and become JSON null in `frame_to_records`,
    # which is what Recharts needs to draw a gap rather than a drop to zero.
    wide = wide.reindex(columns=kept).reset_index()
    wide["period"] = wide["period"].astype(str)
    wide.columns = [str(column) for column in wide.columns]
    wide = wide.sort_values("period")

    payload = bound_rows(wide, context.max_rows)
    return {
        "period": args.period,
        "date_column": date_column,
        "metric": metric,
        "agg": args.agg,
        "group_by": group_column,
        "series_keys": [str(key) for key in kept],
        "groups_omitted": max(0, int(totals.size) - len(kept)),
        "value_label": f"{args.agg}({metric})" if metric else "count",
        **payload,
    }


__all__ = ["aggregate", "correlation", "detect_outliers", "time_series", "top_n"]
