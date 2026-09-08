"""Scripted LLM provider.

Two jobs, and it is worth being explicit about why one class does both:

* **In tests** it is *scripted*: you hand it the exact sequence of answers and
  assert on what the loop did with them. That is what makes the agent loop
  testable at all — a real model is non-deterministic, so a test written against
  one asserts nothing.
* **In the offline demo** it is *heuristic*: given no script, it inspects the
  question and the tool results and plays a plausible short analysis. It reads
  the real schema of the loaded dataset and quotes the real numbers the tools
  returned, so the demo is honest: no figure is invented here either.

It never reaches the network, which is exactly why CI needs no secret.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Sequence
from typing import Any

from app.exceptions import LLMError
from app.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

ScriptStep = LLMResponse
Script = Sequence[ScriptStep] | Callable[[list[LLMMessage]], LLMResponse]

#: Columns the demo prefers as "the" money metric, best first.
_PREFERRED_METRICS = ("revenue", "amount", "sales", "total", "turnover", "montant")

#: Question keywords -> analysis plan. Written without accents because the
#: question is de-accented before matching.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "outliers": ("aberrant", "outlier", "anomal", "extreme", "bizarre"),
    "correlation": ("correl", "lien entre", "relation entre"),
    "time_series": ("mois", "month", "evolution", "tendance", "trend", "temps", "quand"),
    "breakdown": (
        "region",
        "pays",
        "country",
        "categor",
        "canal",
        "channel",
        "segment",
        "produit",
        "product",
        "repartition",
        "meilleur",
        "best",
        "top",
        "performe",
    ),
}


def _deaccent(text: str) -> str:
    """Strip accents so « région » matches the column named `region`."""
    normalised = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in normalised if not unicodedata.combining(char))


def _humanise(value: Any) -> str:
    """Format a number the way the demo text should read it."""
    if isinstance(value, int | float):
        return f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return str(value)


class FakeLLMProvider(LLMProvider):
    """Deterministic provider: scripted for tests, heuristic for the offline demo."""

    name = "fake"

    def __init__(self, script: Script | None = None) -> None:
        self._script = script
        self._cursor = 0
        #: Every `complete` call, so tests can assert on what the loop sent.
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """Return the next scripted answer, or an improvised one."""
        self.calls.append({"system": system, "messages": messages, "tools": tools})

        if callable(self._script):
            return self._script(messages)
        if self._script is not None:
            if self._cursor >= len(self._script):
                raise LLMError(
                    "Le fournisseur scripté n'a plus de réponse à donner.",
                    detail="Le script du FakeLLMProvider est épuisé : allongez-le dans le test.",
                )
            step = self._script[self._cursor]
            self._cursor += 1
            return step
        return _improvise(messages, {tool["name"] for tool in tools})


# ---------------------------------------------------------------------------
# Heuristic mode
# ---------------------------------------------------------------------------


def _improvise(messages: list[LLMMessage], available: set[str]) -> LLMResponse:
    """Play a short, plausible analysis based on the question and past results."""
    question = _deaccent(_first_user_text(messages))
    outcomes = _tool_outcomes(messages)
    step = len(outcomes)

    if step == 0:
        return _tool_call("list_columns", {})

    schema = outcomes[0].get("payload") if outcomes[0].get("ok") else None
    columns = schema.get("columns", []) if isinstance(schema, dict) else []
    plan = _classify(question)

    if step == 1:
        call = _analysis_call(plan, question, columns)
        if call is None:
            return _final_text(question, outcomes, columns)
        return call

    analysis = outcomes[1]
    if step == 2 and "make_chart" in available and _chartable(plan, analysis):
        return _chart_call(plan, analysis)

    return _final_text(question, outcomes, columns)


def _first_user_text(messages: list[LLMMessage]) -> str:
    for message in messages:
        if message.role == "user":
            for block in message.content:
                if isinstance(block, TextBlock):
                    return block.text
    return ""


def _tool_outcomes(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Collect every tool result seen so far, in order, decoded when possible."""
    outcomes: list[dict[str, Any]] = []
    for message in messages:
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            try:
                payload = json.loads(block.content)
            except json.JSONDecodeError:
                payload = {"raw": block.content}
            outcomes.append({"ok": not block.is_error, "payload": payload})
    return outcomes


def _classify(question: str) -> str:
    for plan, needles in _KEYWORDS.items():
        if any(needle in question for needle in needles):
            return plan
    return "breakdown"


def _pick(columns: list[dict[str, Any]], kind: str) -> list[str]:
    return [column["name"] for column in columns if column.get("kind") == kind]


def _metric_column(columns: list[dict[str, Any]]) -> str | None:
    numeric = _pick(columns, "numeric")
    for preferred in _PREFERRED_METRICS:
        for name in numeric:
            if preferred in name.lower():
                return name
    return numeric[0] if numeric else None


def _mentions(question: str, name: str) -> bool:
    """True when the question seems to name this column.

    The 5-character prefix is a poor man's stemmer: it is what makes « produits »
    match the `product` column and « régions » match `region`, without pulling in
    a morphology library for a demo provider.
    """
    candidate = _deaccent(name.replace("_", " "))
    if candidate in question:
        return True
    return len(candidate) >= 5 and candidate[:5] in question


def _mentioned_columns(question: str, columns: list[dict[str, Any]], kind: str) -> list[str]:
    """Columns of `kind` that the question appears to name, in schema order."""
    return [name for name in _pick(columns, kind) if _mentions(question, name)]


def _dimension_column(question: str, columns: list[dict[str, Any]]) -> str | None:
    """Pick the categorical column the question names, else the most readable one."""
    named = _mentioned_columns(question, columns, "categorical")
    if named:
        return named[0]
    # Prefer a column with few, meaningful modalities over an id-like column.
    ranked = sorted(
        (column for column in columns if column.get("kind") == "categorical"),
        key=lambda column: abs(column.get("cardinality", 999) - 6),
    )
    return ranked[0]["name"] if ranked else None


def _tool_call(name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content=[ToolUseBlock(id=f"toolu_fake_{name}", name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


def _analysis_call(plan: str, question: str, columns: list[dict[str, Any]]) -> LLMResponse | None:
    metric = _metric_column(columns)
    numeric = _pick(columns, "numeric")
    dates = _pick(columns, "datetime")

    if plan == "outliers" and numeric:
        named = _mentioned_columns(question, columns, "numeric")
        target = (
            named[0]
            if named
            else next(
                (name for name in numeric if "price" in name.lower() or "prix" in name.lower()),
                numeric[0],
            )
        )
        return _tool_call("detect_outliers", {"column": target, "method": "iqr", "factor": 1.5})

    if plan == "correlation" and len(numeric) >= 2:
        named = _mentioned_columns(question, columns, "numeric")
        pair = (named + [name for name in numeric if name not in named])[:2]
        return _tool_call("correlation", {"column_a": pair[0], "column_b": pair[1]})

    if plan == "time_series" and dates and metric:
        return _tool_call(
            "time_series",
            {"date_column": dates[0], "period": "month", "agg": "sum", "metric": metric},
        )

    dimension = _dimension_column(question, columns)
    if dimension and metric:
        return _tool_call(
            "aggregate",
            {"group_by": [dimension], "agg": "sum", "metric": metric, "limit": 12},
        )
    if dimension:
        return _tool_call("aggregate", {"group_by": [dimension], "agg": "count", "limit": 12})
    return None


def _chartable(plan: str, analysis: dict[str, Any]) -> bool:
    payload = analysis.get("payload")
    return (
        analysis.get("ok")
        and plan in {"time_series", "breakdown"}
        and isinstance(payload, dict)
        and bool(payload.get("rows"))
    )


def _chart_call(plan: str, analysis: dict[str, Any]) -> LLMResponse:
    payload: dict[str, Any] = analysis["payload"]
    rows: list[dict[str, Any]] = payload["rows"]
    keys = list(rows[0])
    if plan == "time_series":
        series_keys = [key for key in payload.get("series_keys", ["value"]) if key in rows[0]]
        return _tool_call(
            "make_chart",
            {
                "result_id": payload["result_id"],
                "type": "line",
                "title": f"Évolution de {payload.get('value_label', 'la métrique')} par mois",
                "x_key": "period",
                "series": [{"key": key} for key in series_keys or ["value"]],
                "y_label": payload.get("value_label"),
            },
        )
    x_key = next((key for key in keys if key != "value"), keys[0])
    return _tool_call(
        "make_chart",
        {
            "result_id": payload["result_id"],
            "type": "bar",
            "title": f"{payload.get('value_label', 'Total')} par {x_key}",
            "x_key": x_key,
            "series": [{"key": "value", "label": payload.get("value_label", "valeur")}],
            "y_label": payload.get("value_label"),
        },
    )


def _final_text(
    question: str, outcomes: list[dict[str, Any]], columns: list[dict[str, Any]]
) -> LLMResponse:
    """Write the closing answer strictly from the numbers the tools returned."""
    lines: list[str] = []
    analysis = outcomes[1] if len(outcomes) > 1 else None
    payload = analysis.get("payload") if isinstance(analysis, dict) else None

    if analysis is not None and not analysis.get("ok"):
        lines.append(
            "Je n'ai pas pu mener l'analyse à son terme : "
            f"{payload.get('error', payload) if isinstance(payload, dict) else payload}"
        )
    elif isinstance(payload, dict) and payload.get("rows"):
        rows = payload["rows"]
        keys = list(rows[0])
        label_key = next((key for key in keys if key != "value"), keys[0])
        lines.append(f"Voici ce que disent les données ({payload.get('value_label', 'valeur')}) :")
        for row in rows[:5]:
            lines.append(f"- {row[label_key]} : {_humanise(row.get('value'))}")
        if payload.get("truncated"):
            lines.append(f"({payload['row_count']} lignes affichées sur {payload['total_rows']}.)")
    elif isinstance(payload, dict) and "outlier_count" in payload:
        bounds = payload.get("bounds", {})
        lines.append(
            f"J'ai trouvé {payload['outlier_count']} valeurs aberrantes sur « {payload['column']} » "
            f"(méthode {payload['method']}), soit {payload['share']:.2%} des valeurs. "
            f"L'intervalle attendu va de {_humanise(bounds.get('lower'))} à {_humanise(bounds.get('upper'))}."
        )
        for example in payload.get("examples", [])[:3]:
            lines.append(f"- {example}")
    elif isinstance(payload, dict) and "pearson_r" in payload:
        lines.append(
            f"La corrélation de Pearson entre « {payload['column_a']} » et « {payload['column_b']} » "
            f"vaut {payload['pearson_r']} sur {payload['n']} lignes : {payload['interpretation']}. "
            f"{payload['caveat']}"
        )
    else:
        names = ", ".join(column["name"] for column in columns[:8]) or "aucune colonne lisible"
        lines.append(
            "Je n'ai pas trouvé d'angle d'analyse évident pour cette question. "
            f"Le dataset contient notamment : {names}."
        )

    lines.append("")
    lines.append(
        "(Réponse produite par le fournisseur « fake », hors ligne : les chiffres viennent "
        "des outils Python, la rédaction est scriptée.)"
    )
    return LLMResponse(content=[TextBlock(text="\n".join(lines))], stop_reason="end_turn")
