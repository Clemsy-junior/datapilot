"""Unit tests for `aggregate`, `top_n`, `correlation`, `detect_outliers`, `time_series`."""

from __future__ import annotations

import pytest

from app.exceptions import ToolError
from app.tools.registry import ToolContext, registry


def run(name: str, arguments: dict, context: ToolContext) -> dict:
    """Dispatch a tool and return only its result payload."""
    result, _duration = registry.dispatch(name, arguments, context)
    return result


class TestAggregate:
    def test_sum_by_region(self, context: ToolContext) -> None:
        result = run(
            "aggregate",
            {"group_by": ["region"], "agg": "sum", "metric": "revenue"},
            context,
        )

        assert result["rows"] == [
            {"region": "APAC", "value": pytest.approx(5130.0)},
            {"region": "EMEA", "value": pytest.approx(220.0)},
        ]
        assert result["value_label"] == "sum(revenue)"
        assert result["truncated"] is False

    def test_mean_and_ascending_sort(self, context: ToolContext) -> None:
        result = run(
            "aggregate",
            {
                "group_by": ["category"],
                "agg": "mean",
                "metric": "revenue",
                "sort": "value_asc",
            },
            context,
        )

        assert [row["category"] for row in result["rows"]] == ["Home", "Tech"]
        assert result["rows"][0]["value"] == pytest.approx(50.0)

    def test_count_needs_no_metric(self, context: ToolContext) -> None:
        result = run("aggregate", {"group_by": ["region"], "agg": "count"}, context)

        assert {row["region"]: row["value"] for row in result["rows"]} == {"APAC": 3, "EMEA": 3}
        assert result["value_label"] == "count"

    def test_two_grouping_columns(self, context: ToolContext) -> None:
        result = run(
            "aggregate",
            {"group_by": ["region", "category"], "agg": "sum", "metric": "revenue"},
            context,
        )

        assert result["total_rows"] == 4
        assert {"region", "category", "value"} == set(result["rows"][0])

    def test_limit_is_capped_by_the_row_budget(self, store) -> None:
        narrow = ToolContext(store=store, dataset_id="tiny", max_rows=1)
        result = run(
            "aggregate",
            {"group_by": ["region"], "agg": "sum", "metric": "revenue", "limit": 50},
            narrow,
        )

        assert result["row_count"] == 1
        assert result["total_rows"] == 2
        assert result["truncated"] is True

    def test_sum_without_metric_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("aggregate", {"group_by": ["region"], "agg": "sum"}, context)

        assert "metric" in (excinfo.value.detail or "")

    def test_metric_must_be_numeric(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "aggregate",
                {"group_by": ["region"], "agg": "sum", "metric": "category"},
                context,
            )

        assert "numérique" in excinfo.value.message

    def test_duplicate_grouping_columns_are_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run(
                "aggregate",
                {"group_by": ["region", "region"], "agg": "count"},
                context,
            )

    def test_unknown_group_column_is_a_tool_error(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run("aggregate", {"group_by": ["zone"], "agg": "count"}, context)


class TestTopN:
    def test_top_rows(self, context: ToolContext) -> None:
        result = run(
            "top_n",
            {"metric": "revenue", "n": 2, "columns": ["order_id"]},
            context,
        )

        assert result["mode"] == "rows"
        assert [row["order_id"] for row in result["rows"]] == ["O4", "O2"]

    def test_bottom_rows(self, context: ToolContext) -> None:
        result = run(
            "top_n",
            {"metric": "revenue", "n": 1, "direction": "bottom", "columns": ["order_id"]},
            context,
        )

        assert result["rows"][0]["order_id"] == "O1"

    def test_top_groups(self, context: ToolContext) -> None:
        result = run(
            "top_n",
            {"metric": "revenue", "group_by": "category", "n": 1},
            context,
        )

        assert result["mode"] == "groups"
        assert result["rows"] == [{"category": "Tech", "value": pytest.approx(5200.0)}]

    def test_non_numeric_metric_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run("top_n", {"metric": "region"}, context)

    def test_n_is_validated(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("top_n", {"metric": "revenue", "n": 0}, context)

        assert "n" in (excinfo.value.detail or "")


class TestCorrelation:
    def test_returns_coefficient_and_interpretation(self, context: ToolContext) -> None:
        result = run(
            "correlation",
            {"column_a": "quantity", "column_b": "unit_price"},
            context,
        )

        assert result["n"] == 6
        assert -1.0 <= result["pearson_r"] <= 1.0
        assert "corrélation" in result["interpretation"]
        assert "causalité" in result["caveat"]

    def test_perfectly_correlated_columns(self, context: ToolContext) -> None:
        # unit_price and revenue move together on this dataset except for quantity,
        # so the coefficient must be strongly positive.
        result = run(
            "correlation",
            {"column_a": "unit_price", "column_b": "revenue"},
            context,
        )

        assert result["pearson_r"] > 0.9

    def test_constant_column_is_refused_instead_of_dividing_by_zero(
        self, context: ToolContext
    ) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("correlation", {"column_a": "tax_rate", "column_b": "revenue"}, context)

        assert "constante" in excinfo.value.message

    def test_correlating_a_column_with_itself_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run("correlation", {"column_a": "revenue", "column_b": "revenue"}, context)

    def test_too_few_rows_is_refused(self, context: ToolContext) -> None:
        selection = run(
            "filter_rows",
            {"conditions": [{"column": "order_id", "op": "eq", "value": "O1"}]},
            context,
        )["selection_id"]

        with pytest.raises(ToolError) as excinfo:
            run(
                "correlation",
                {"column_a": "quantity", "column_b": "revenue", "selection_id": selection},
                context,
            )

        assert "Trop peu de lignes" in excinfo.value.message

    def test_non_numeric_column_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run("correlation", {"column_a": "region", "column_b": "revenue"}, context)


class TestDetectOutliers:
    def test_iqr_flags_the_extreme_value(self, context: ToolContext) -> None:
        result = run(
            "detect_outliers",
            {"column": "revenue", "columns": ["order_id"]},
            context,
        )

        assert result["outlier_count"] == 1
        assert result["examples"][0]["order_id"] == "O4"
        assert result["bounds"]["upper"] == pytest.approx(178.75)
        assert result["share"] == pytest.approx(1 / 6, abs=1e-6)

    def test_zscore_method(self, context: ToolContext) -> None:
        result = run(
            "detect_outliers",
            {"column": "revenue", "method": "zscore", "factor": 1.5},
            context,
        )

        assert result["method"] == "zscore"
        assert result["outlier_count"] >= 1
        assert "std" in result["bounds"]

    def test_constant_column_iqr_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("detect_outliers", {"column": "tax_rate"}, context)

        assert "interquartile" in excinfo.value.message

    def test_constant_column_zscore_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run("detect_outliers", {"column": "tax_rate", "method": "zscore"}, context)

        assert "écart-type" in excinfo.value.message

    def test_too_few_values_is_refused(self, context: ToolContext) -> None:
        selection = run(
            "filter_rows",
            {"conditions": [{"column": "region", "op": "eq", "value": "EMEA"}]},
            context,
        )["selection_id"]

        with pytest.raises(ToolError) as excinfo:
            run("detect_outliers", {"column": "revenue", "selection_id": selection}, context)

        assert "Trop peu de valeurs" in excinfo.value.message

    def test_factor_must_be_positive(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run("detect_outliers", {"column": "revenue", "factor": 0}, context)


class TestTimeSeries:
    def test_monthly_sum(self, context: ToolContext) -> None:
        result = run(
            "time_series",
            {"date_column": "order_date", "period": "month", "agg": "sum", "metric": "revenue"},
            context,
        )

        assert result["rows"] == [
            {"period": "2024-01", "value": pytest.approx(120.0)},
            {"period": "2024-02", "value": pytest.approx(5030.0)},
            {"period": "2024-03", "value": pytest.approx(200.0)},
        ]
        assert result["series_keys"] == ["value"]

    def test_daily_count(self, context: ToolContext) -> None:
        result = run(
            "time_series",
            {"date_column": "order_date", "period": "day", "agg": "count"},
            context,
        )

        assert result["total_rows"] == 6
        assert all(row["value"] == 1 for row in result["rows"])

    def test_grouped_series_are_pivoted(self, context: ToolContext) -> None:
        result = run(
            "time_series",
            {
                "date_column": "order_date",
                "period": "month",
                "agg": "sum",
                "metric": "revenue",
                "group_by": "region",
            },
            context,
        )

        assert set(result["series_keys"]) == {"EMEA", "APAC"}
        january = next(row for row in result["rows"] if row["period"] == "2024-01")
        assert january["EMEA"] == pytest.approx(120.0)
        # No APAC order in January: a gap, not a zero.
        assert january["APAC"] is None

    def test_non_datetime_column_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "time_series", {"date_column": "region", "period": "month", "agg": "count"}, context
            )

        assert "dates" in excinfo.value.message

    def test_invalid_period_is_refused(self, context: ToolContext) -> None:
        with pytest.raises(ToolError):
            run(
                "time_series",
                {"date_column": "order_date", "period": "quarter", "agg": "count"},
                context,
            )
