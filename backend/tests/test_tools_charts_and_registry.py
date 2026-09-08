"""Unit tests for `make_chart`, the registry itself and the dataset store."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import BaseModel

from app.datasets.store import DatasetStore
from app.exceptions import DatasetNotFoundError, InvalidDatasetError, ToolError
from app.models.chart import ChartSpec
from app.tools.registry import ToolContext, ToolRegistry, registry


def run(name: str, arguments: dict, context: ToolContext) -> dict:
    """Dispatch a tool and return only its result payload."""
    result, _duration = registry.dispatch(name, arguments, context)
    return result


@pytest.fixture
def aggregate_result(context: ToolContext) -> dict:
    """A `sum(revenue) by region` result, ready to be charted."""
    return run("aggregate", {"group_by": ["region"], "agg": "sum", "metric": "revenue"}, context)


class TestMakeChart:
    def test_builds_a_spec_from_a_previous_result(
        self, context: ToolContext, aggregate_result: dict
    ) -> None:
        result = run(
            "make_chart",
            {
                "result_id": aggregate_result["result_id"],
                "type": "bar",
                "title": "CA par région",
                "x_key": "region",
                "series": [{"key": "value", "label": "Chiffre d'affaires"}],
            },
            context,
        )

        chart = ChartSpec.model_validate(result["chart"])
        assert chart.type == "bar"
        assert chart.x_key == "region"
        assert [series.label for series in chart.series] == ["Chiffre d'affaires"]
        # The data is the tool's own rows, not something the caller supplied.
        assert chart.data == aggregate_result["rows"]

    @pytest.mark.parametrize("chart_type", ["bar", "line", "area", "scatter", "pie"])
    def test_every_chart_type_is_accepted(
        self, context: ToolContext, aggregate_result: dict, chart_type: str
    ) -> None:
        result = run(
            "make_chart",
            {
                "result_id": aggregate_result["result_id"],
                "type": chart_type,
                "title": f"Test {chart_type}",
                "x_key": "region",
                "series": [{"key": "value"}],
            },
            context,
        )

        assert result["chart"]["type"] == chart_type

    def test_unknown_result_id_is_refused_with_the_available_ids(
        self, context: ToolContext, aggregate_result: dict
    ) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "make_chart",
                {
                    "result_id": "res_999",
                    "type": "bar",
                    "title": "Fantôme",
                    "x_key": "region",
                    "series": [{"key": "value"}],
                },
                context,
            )

        assert aggregate_result["result_id"] in (excinfo.value.detail or "")

    def test_unknown_series_key_is_refused(
        self, context: ToolContext, aggregate_result: dict
    ) -> None:
        with pytest.raises(ToolError) as excinfo:
            run(
                "make_chart",
                {
                    "result_id": aggregate_result["result_id"],
                    "type": "bar",
                    "title": "Mauvaise clé",
                    "x_key": "region",
                    "series": [{"key": "chiffre_affaires"}],
                },
                context,
            )

        assert "chiffre_affaires" in excinfo.value.message

    def test_a_result_without_rows_cannot_be_charted(self, context: ToolContext) -> None:
        correlation = run("correlation", {"column_a": "quantity", "column_b": "revenue"}, context)

        with pytest.raises(ToolError) as excinfo:
            run(
                "make_chart",
                {
                    "result_id": correlation["result_id"],
                    "type": "bar",
                    "title": "Impossible",
                    "x_key": "value",
                    "series": [{"key": "value"}],
                },
                context,
            )

        assert "traçables" in excinfo.value.message

    def test_truncation_is_carried_into_the_chart_note(self, store: DatasetStore) -> None:
        narrow = ToolContext(store=store, dataset_id="tiny", max_rows=1)
        source = run(
            "aggregate",
            {"group_by": ["region"], "agg": "sum", "metric": "revenue"},
            narrow,
        )

        result = run(
            "make_chart",
            {
                "result_id": source["result_id"],
                "type": "bar",
                "title": "Tronqué",
                "x_key": "region",
                "series": [{"key": "value"}],
            },
            narrow,
        )

        assert "1 des 2 lignes" in result["chart"]["note"]


class TestChartSpecModel:
    def test_rejects_a_spec_whose_keys_are_absent_from_the_data(self) -> None:
        with pytest.raises(ValueError, match="absent"):
            ChartSpec(
                type="bar",
                title="Incohérent",
                x_key="region",
                series=[{"key": "value", "label": "valeur"}],
                data=[{"zone": "EMEA", "total": 1}],
            )


class TestRegistry:
    def test_every_expected_tool_is_registered(self) -> None:
        assert registry.names() == [
            "aggregate",
            "correlation",
            "describe_column",
            "detect_outliers",
            "filter_rows",
            "list_columns",
            "make_chart",
            "time_series",
            "top_n",
        ]

    def test_schemas_are_llm_ready(self) -> None:
        schemas = {schema["name"]: schema for schema in registry.schemas()}

        assert set(schemas) == set(registry.names())
        for schema in schemas.values():
            assert schema["description"]
            assert schema["input_schema"]["type"] == "object"

    def test_unknown_tool_lists_the_available_ones(self, context: ToolContext) -> None:
        with pytest.raises(ToolError) as excinfo:
            registry.dispatch("do_magic", {}, context)

        assert "aggregate" in (excinfo.value.detail or "")

    def test_results_are_recorded_with_incrementing_ids(self, context: ToolContext) -> None:
        first = run("list_columns", {}, context)
        second = run("list_columns", {}, context)

        assert first["result_id"] == "res_1"
        assert second["result_id"] == "res_2"
        assert set(context.results) == {"res_1", "res_2"}

    def test_dispatch_measures_duration(self, context: ToolContext) -> None:
        _result, duration_ms = registry.dispatch("list_columns", {}, context)

        assert duration_ms >= 0.0

    def test_a_handler_crash_becomes_a_tool_error(self, context: ToolContext) -> None:
        local = ToolRegistry()

        class NoArgs(BaseModel):
            pass

        @local.register(name="boom", description="Explose.", args_model=NoArgs)
        def _boom(_args: NoArgs, _context: ToolContext) -> dict:
            raise ZeroDivisionError("division by zero")

        with pytest.raises(ToolError) as excinfo:
            local.dispatch("boom", {}, context)

        assert "boom" in excinfo.value.message

    def test_registering_the_same_name_twice_is_refused(self) -> None:
        local = ToolRegistry()

        class NoArgs(BaseModel):
            pass

        @local.register(name="once", description="…", args_model=NoArgs)
        def _once(_args: NoArgs, _context: ToolContext) -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):
            local.register(name="once", description="…", args_model=NoArgs)(_once)


class TestDatasetStore:
    def test_discover_loads_bundled_csv(self, store: DatasetStore) -> None:
        datasets = store.list_datasets()

        assert [dataset.id for dataset in datasets] == ["tiny"]
        assert datasets[0].row_count == 6
        assert datasets[0].source == "bundled"

    def test_dates_are_typed_at_load_time(self, store: DatasetStore) -> None:
        frame = store.frame("tiny")

        assert pd.api.types.is_datetime64_any_dtype(frame["order_date"])

    def test_schema_preview_is_capped_at_ten_rows(self, store: DatasetStore) -> None:
        schema = store.schema("tiny")

        assert len(schema.preview) == 6
        assert {column.name for column in schema.columns} >= {"region", "revenue"}

    def test_unknown_dataset_is_reported(self, store: DatasetStore) -> None:
        with pytest.raises(DatasetNotFoundError):
            store.frame("nope")

    def test_upload_registers_a_new_dataset(self, store: DatasetStore) -> None:
        info = store.add_upload("extra.csv", b"a,b\n1,2\n3,4\n")

        assert info.source == "upload"
        assert info.row_count == 2
        assert store.frame(info.id).shape == (2, 2)

    def test_empty_upload_is_refused(self, store: DatasetStore) -> None:
        with pytest.raises(InvalidDatasetError):
            store.add_upload("empty.csv", b"   ")

    def test_unparsable_upload_is_refused(self, store: DatasetStore) -> None:
        with pytest.raises(InvalidDatasetError):
            store.add_upload("broken.csv", b"a,b\n1,2,3,4\n5\n\x00\xff")

    def test_a_malformed_bundled_file_does_not_break_discovery(self, data_dir) -> None:
        (data_dir / "broken.csv").write_bytes(b"a,b\n1,2,3,4,5\n6\n")
        store = DatasetStore(data_dir)

        store.discover()

        assert "tiny" in {dataset.id for dataset in store.list_datasets()}
