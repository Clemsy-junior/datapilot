"""Unit tests for `list_columns`, `describe_column` and `filter_rows`."""

from __future__ import annotations

import pytest

from app.exceptions import ToolError
from app.tools.registry import ToolContext, registry


def run(name: str, arguments: dict, context: ToolContext) -> dict:
    """Dispatch a tool and return only its result payload."""
    result, _duration = registry.dispatch(name, arguments, context)
    return result


class TestListColumns:
    def test_reports_kind_nulls_and_cardinality(self, context: ToolContext) -> None:
        result = run("list_columns", {}, context)
        by_name = {column["name"]: column for column in result["columns"]}

        assert result["row_count"] == 6
        assert result["column_count"] == 9
        assert by_name["order_date"]["kind"] == "datetime"
        assert by_name["region"]["kind"] == "categorical"
        assert by_name["revenue"]["kind"] == "numeric"
        assert by_name["delivery_days"]["null_count"] == 1
        assert by_name["region"]["cardinality"] == 2

    def test_truncates_when_over_the_row_budget(self, store) -> None:
        narrow = ToolContext(store=store, dataset_id="tiny", max_rows=3)
        result = run("list_columns", {}, narrow)

        assert len(result["columns"]) == 3
        assert result["truncated"] is True


class TestDescribeColumn:
    def test_numeric_statistics(self, context: ToolContext) -> None:
        result = run("describe_column", {"column": "revenue"}, context)

        assert result["kind"] == "numeric"
        assert result["count"] == 6
        assert result["statistics"]["sum"] == pytest.approx(5350.0)
        assert result["statistics"]["min"] == pytest.approx(20.0)
        assert result["statistics"]["max"] == pytest.approx(5000.0)
        assert result["statistics"]["median"] == pytest.approx(100.0)

    def test_categorical_distribution(self, context: ToolContext) -> None:
        result = run("describe_column", {"column": "region"}, context)

        assert result["kind"] == "categorical"
        assert {entry["value"] for entry in result["top_values"]} == {"EMEA", "APAC"}
        assert all(entry["count"] == 3 for entry in result["top_values"])
        assert result["top_values"][0]["share"] == pytest.approx(0.5)

    def test_datetime_span(self, context: ToolContext) -> None:
        result = run("describe_column", {"column": "order_date"}, context)

        assert result["kind"] == "datetime"
        assert result["statistics"]["min"].startswith("2024-01-05")
        assert result["statistics"]["span_days"] == 66

    def test_counts_nulls_separately(self, context: ToolContext) -> None:
        result = run("describe_column", {"column": "delivery_days"}, context)

        assert result["count"] == 5
        assert result["null_count"] == 1

    def test_unknown_column_is_a_tool_error_not_a_crash(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("describe_column", {"column": "regionn"}, context)

        # The message must be usable by the model: it names the mistake and the fix.
        assert "regionn" in excinfo.value.message
        assert "region" in (excinfo.value.detail or "")

    def test_missing_required_argument_is_reported_clearly(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("describe_column", {}, context)

        assert "column" in (excinfo.value.detail or "")


class TestFilterRows:
    def test_returns_a_summary_and_a_reusable_selection(self, context: ToolContext) -> None:
        result = run(
            "filter_rows",
            {"conditions": [{"column": "region", "op": "eq", "value": "EMEA"}]},
            context,
        )

        assert result["matched_rows"] == 3
        assert result["total_rows"] == 6
        assert result["share"] == pytest.approx(0.5)
        assert result["selection_id"].startswith("sel_")
        # A summary, not a data dump: at most five preview rows.
        assert len(result["preview"]) <= 5

    def test_selection_narrows_a_later_tool(self, context: ToolContext) -> None:
        selection = run(
            "filter_rows",
            {"conditions": [{"column": "region", "op": "eq", "value": "EMEA"}]},
            context,
        )["selection_id"]

        described = run(
            "describe_column",
            {"column": "revenue", "selection_id": selection},
            context,
        )

        assert described["count"] == 3
        assert described["statistics"]["sum"] == pytest.approx(220.0)

    def test_and_or_combination(self, context: ToolContext) -> None:
        conditions = [
            {"column": "region", "op": "eq", "value": "EMEA"},
            {"column": "revenue", "op": "gt", "value": 50},
        ]

        conjunction = run("filter_rows", {"conditions": conditions, "combine": "and"}, context)
        disjunction = run("filter_rows", {"conditions": conditions, "combine": "or"}, context)

        assert conjunction["matched_rows"] == 2
        assert disjunction["matched_rows"] == 5

    def test_is_null_operator(self, context: ToolContext) -> None:
        result = run(
            "filter_rows",
            {"conditions": [{"column": "delivery_days", "op": "is_null"}]},
            context,
        )

        assert result["matched_rows"] == 1

    def test_date_comparison_accepts_iso_strings(self, context: ToolContext) -> None:
        result = run(
            "filter_rows",
            {"conditions": [{"column": "order_date", "op": "gte", "value": "2024-02-01"}]},
            context,
        )

        assert result["matched_rows"] == 4

    def test_empty_match_is_not_an_error(self, context: ToolContext) -> None:
        result = run(
            "filter_rows",
            {"conditions": [{"column": "region", "op": "eq", "value": "LATAM"}]},
            context,
        )

        assert result["matched_rows"] == 0
        assert "Aucune ligne" in result["note"]

    def test_ordering_a_categorical_column_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "filter_rows",
                {"conditions": [{"column": "region", "op": "gt", "value": "EMEA"}]},
                context,
            )

        assert "catégorielle" in excinfo.value.message

    def test_non_numeric_value_on_numeric_column_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run(
                "filter_rows",
                {"conditions": [{"column": "revenue", "op": "gt", "value": "beaucoup"}]},
                context,
            )

    def test_invalid_date_is_refused_with_a_format_hint(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "filter_rows",
                {"conditions": [{"column": "order_date", "op": "gte", "value": "hier"}]},
                context,
            )

        assert "ISO" in (excinfo.value.detail or "")

    def test_in_operator_requires_a_list(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "filter_rows",
                {"conditions": [{"column": "region", "op": "in", "value": "EMEA"}]},
                context,
            )

        assert "liste" in excinfo.value.message

    def test_unknown_selection_id_is_reported(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("describe_column", {"column": "revenue", "selection_id": "sel_nope"}, context)

        assert "sel_nope" in excinfo.value.message

    def test_analysing_an_empty_selection_is_refused(self, context: ToolContext) -> None:
        selection = run(
            "filter_rows",
            {"conditions": [{"column": "region", "op": "eq", "value": "LATAM"}]},
            context,
        )["selection_id"]

        with pytest.raises(ToolError) as excinfo:
            run("describe_column", {"column": "revenue", "selection_id": selection}, context)

        assert "vide" in (excinfo.value.detail or "")
