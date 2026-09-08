"""Chart tool: `make_chart`.

The important design decision lives here. `make_chart` does **not** accept data
points. It accepts the `result_id` of a tool result produced earlier in the same
turn, and reads the rows from there.

That single constraint is what makes the project's central claim enforceable
rather than aspirational: the model can choose *what* to plot, but it physically
cannot put a number on a chart that a deterministic Python tool did not compute.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.exceptions import ToolError
from app.models.chart import ChartSeries, ChartSpec, ChartType
from app.tools.registry import ToolContext, registry


class SeriesArg(BaseModel):
    """One series to draw, referencing a key of the source rows."""

    key: str = Field(description="Clé présente dans les lignes du résultat source.")
    label: str | None = Field(default=None, description="Nom affiché dans la légende.")


class MakeChartArgs(BaseModel):
    """Arguments of `make_chart`."""

    result_id: str = Field(
        description="result_id d'un résultat d'outil précédent, dont les lignes seront tracées."
    )
    type: ChartType
    title: str = Field(min_length=1, max_length=120)
    x_key: str = Field(description="Clé de l'axe des abscisses dans les lignes source.")
    series: list[SeriesArg] = Field(min_length=1, max_length=8)
    x_label: str | None = None
    y_label: str | None = None


@registry.register(
    name="make_chart",
    description=(
        "Produit une spécification de graphique (bar, line, area, scatter, pie) que "
        "l'interface rend avec Recharts. Ne prend AUCUNE donnée en argument : indiquez "
        "le result_id d'un résultat d'outil précédent, ainsi que la clé des abscisses et "
        "les clés des séries à tracer. Appelez d'abord aggregate, top_n ou time_series."
    ),
    args_model=MakeChartArgs,
)
def make_chart(args: MakeChartArgs, context: ToolContext) -> dict[str, Any]:
    """Build a `ChartSpec` from rows a previous tool already computed."""
    source = context.results.get(args.result_id)
    if source is None:
        available = ", ".join(sorted(context.results)) or "aucun"
        raise ToolError(
            f"Le résultat « {args.result_id} » n'existe pas dans ce tour.",
            detail=(
                f"Résultats disponibles : {available}. Appelez d'abord un outil "
                "d'analyse (aggregate, top_n, time_series) et réutilisez son result_id."
            ),
        )

    rows = source.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ToolError(
            f"Le résultat « {args.result_id} » ne contient pas de lignes traçables.",
            detail="Utilisez un résultat d'aggregate, top_n ou time_series.",
        )

    available_keys = sorted(rows[0])
    missing = [key for key in (args.x_key, *(s.key for s in args.series)) if key not in rows[0]]
    if missing:
        raise ToolError(
            f"Clés absentes des lignes source : {', '.join(sorted(set(missing)))}.",
            detail=f"Clés disponibles : {', '.join(available_keys)}.",
        )

    spec = ChartSpec(
        type=args.type,
        title=args.title,
        x_key=args.x_key,
        series=[ChartSeries(key=s.key, label=s.label or s.key) for s in args.series],
        data=rows,
        x_label=args.x_label,
        y_label=args.y_label,
        note=(
            f"Graphique construit sur {len(rows)} des {source['total_rows']} lignes du résultat."
            if source.get("truncated")
            else None
        ),
    )
    # The spec is returned twice on purpose: `chart` is what the SSE layer emits
    # to the browser, and the summary is what goes back into the model's context
    # (sending the full data twice would just burn tokens).
    return {
        "chart": spec.model_dump(mode="json"),
        "summary": {
            "type": spec.type,
            "title": spec.title,
            "points": len(rows),
            "series": [s.label for s in spec.series],
        },
        "note": "Le graphique est affiché à l'utilisateur ; décrivez-le brièvement.",
    }


__all__ = ["make_chart"]
