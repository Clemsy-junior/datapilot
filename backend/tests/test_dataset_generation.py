"""Tests for the synthetic dataset.

Two things matter here: the generator is reproducible, and the signals the demo
relies on are actually present. The last test also checks that the CSV committed
in `backend/data/` is exactly what the script produces — so nobody can hand-edit
the data without the suite noticing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_dataset.py"
BUNDLED_CSV = REPO_ROOT / "backend" / "data" / "sales.csv"


def _load_generator():
    """Import `scripts/generate_dataset.py` as a module, by path."""
    spec = importlib.util.spec_from_file_location("generate_dataset", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    """The dataset at its shipped size, generated once for the whole module."""
    return generator.generate(rows=generator.DEFAULT_ROWS)


class TestReproducibility:
    def test_the_same_seed_gives_the_same_data(self) -> None:
        first = generator.generate(rows=200, seed=7)
        second = generator.generate(rows=200, seed=7)

        pd.testing.assert_frame_equal(first, second)

    def test_a_different_seed_gives_different_data(self) -> None:
        first = generator.generate(rows=200, seed=7)
        second = generator.generate(rows=200, seed=8)

        assert not first["revenue"].equals(second["revenue"])


class TestInjectedSignals:
    def test_expected_columns(self, frame: pd.DataFrame) -> None:
        assert list(frame.columns) == [
            "order_id",
            "order_date",
            "region",
            "country",
            "category",
            "product",
            "channel",
            "quantity",
            "unit_price",
            "discount_rate",
            "revenue",
            "customer_segment",
            "delivery_days",
        ]

    def test_one_region_clearly_dominates(self, frame: pd.DataFrame) -> None:
        by_revenue = frame.groupby("region")["revenue"].sum().sort_values(ascending=False)
        by_orders = frame["region"].value_counts()

        assert by_revenue.index[0] == "North America"
        assert by_revenue.iloc[0] > by_revenue.iloc[1] * 1.3
        # Order volume drives it, so the signal is robust to price noise.
        assert by_orders.iloc[0] > by_orders.iloc[1] * 1.3

    def test_countries_stay_inside_their_region(self, frame: pd.DataFrame) -> None:
        for region, countries in generator.REGIONS.items():
            observed = set(frame.loc[frame["region"] == region, "country"])
            assert observed <= set(countries[1])

    def test_q4_outsells_the_rest_of_the_year(self, frame: pd.DataFrame) -> None:
        months = pd.to_datetime(frame["order_date"]).dt.month
        q4 = frame.loc[months.isin([11, 12]), "revenue"].sum() / 2
        rest = frame.loc[~months.isin([11, 12]), "revenue"].sum() / 10

        assert q4 > rest

    def test_delivery_days_are_about_two_percent_missing(self, frame: pd.DataFrame) -> None:
        assert 0.01 < frame["delivery_days"].isna().mean() < 0.035

    def test_unit_price_carries_extreme_outliers(self, frame: pd.DataFrame) -> None:
        prices = frame["unit_price"]
        q1, q3 = prices.quantile(0.25), prices.quantile(0.75)
        upper = q3 + 1.5 * (q3 - q1)

        assert (prices > upper).sum() > 0
        assert prices.max() > 10 * prices.median()

    def test_revenue_is_consistent_with_its_components(self, frame: pd.DataFrame) -> None:
        expected = (frame["quantity"] * frame["unit_price"] * (1 - frame["discount_rate"])).round(2)

        pd.testing.assert_series_equal(frame["revenue"], expected, check_names=False)


class TestBundledCsv:
    def test_the_committed_csv_is_what_the_script_produces(self) -> None:
        assert BUNDLED_CSV.exists(), "backend/data/sales.csv doit être committé"

        regenerated = generator.generate(rows=generator.DEFAULT_ROWS).to_csv(index=False)

        assert BUNDLED_CSV.read_text(encoding="utf-8") == regenerated
