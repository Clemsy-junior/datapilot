#!/usr/bin/env python3
"""Generate the synthetic e-commerce dataset shipped with DataPilot.

Reproducible by construction: one fixed seed, no wall-clock input, no network.
Running this twice produces byte-identical CSVs, which is what lets the tool
tests assert on real numbers instead of on "roughly".

The data is *deliberately* not clean. Four signals are injected so a demo has
something to find:

* a monthly seasonality peaking in November/December;
* one region (North America) that clearly outperforms the others;
* a handful of absurd `unit_price` values, for `detect_outliers`;
* about 2% of missing `delivery_days`, so null handling is visible.

Usage:
    python scripts/generate_dataset.py [--rows 5000] [--output backend/data/sales.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20240117
DEFAULT_ROWS = 5000
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "sales.csv"

START_DATE = pd.Timestamp("2023-01-01")
END_DATE = pd.Timestamp("2024-12-31")

#: Region -> (relative sales weight, countries). North America is on purpose the
#: strongest region: `aggregate` and `top_n` should find it without ambiguity.
REGIONS: dict[str, tuple[float, list[str]]] = {
    "North America": (0.42, ["United States", "Canada", "Mexico"]),
    "EMEA": (0.28, ["France", "Germany", "United Kingdom", "Spain", "Italy"]),
    "APAC": (0.20, ["Japan", "Australia", "Singapore", "India"]),
    "LATAM": (0.10, ["Brazil", "Argentina", "Chile"]),
}

#: Category -> (weight, mean unit price, price dispersion, products).
CATEGORIES: dict[str, tuple[float, float, float, list[str]]] = {
    "Electronics": (
        0.26,
        240.0,
        0.55,
        ["Laptop Air 13", "Noise Buds", "4K Monitor", "Smart Watch"],
    ),
    "Home": (0.24, 85.0, 0.45, ["Espresso Maker", "Desk Lamp", "Cotton Duvet", "Cast Iron Pan"]),
    "Sports": (0.18, 120.0, 0.50, ["Trail Runners", "Yoga Mat Pro", "Carbon Racket"]),
    "Beauty": (0.17, 45.0, 0.40, ["Serum Vitamin C", "Matte Lipstick", "Argan Shampoo"]),
    "Grocery": (0.15, 22.0, 0.30, ["Arabica Beans 1kg", "Olive Oil 750ml", "Dark Chocolate 90%"]),
}

CHANNELS = ["web", "marketplace", "retail", "partner"]
CHANNEL_WEIGHTS = [0.46, 0.24, 0.20, 0.10]

SEGMENTS = ["Consumer", "SMB", "Enterprise"]
SEGMENT_WEIGHTS = [0.62, 0.27, 0.11]

#: Multiplicative monthly seasonality, January to December. Q4 peaks.
MONTH_SEASONALITY = np.array(
    [0.82, 0.78, 0.92, 0.95, 1.00, 0.97, 0.88, 0.86, 1.02, 1.12, 1.45, 1.60]
)

#: Discount grid. Enterprise buys bigger and negotiates harder.
DISCOUNT_GRID = np.array([0.00, 0.05, 0.10, 0.15, 0.20, 0.30])
DISCOUNT_WEIGHTS = {
    "Consumer": np.array([0.55, 0.20, 0.13, 0.07, 0.04, 0.01]),
    "SMB": np.array([0.35, 0.24, 0.20, 0.12, 0.07, 0.02]),
    "Enterprise": np.array([0.15, 0.18, 0.24, 0.20, 0.15, 0.08]),
}

#: Base delivery time per region, in days (Poisson mean).
DELIVERY_MEAN = {"North America": 3.0, "EMEA": 4.0, "APAC": 6.5, "LATAM": 8.0}

MISSING_DELIVERY_RATE = 0.02
OUTLIER_COUNT = 18
OUTLIER_FACTOR = 22.0


def _weighted_dates(rng: np.random.Generator, rows: int) -> pd.Series:
    """Draw order dates with the monthly seasonality baked in."""
    all_days = pd.date_range(START_DATE, END_DATE, freq="D")
    weights = MONTH_SEASONALITY[all_days.month - 1]
    # A mild weekly effect on top: weekends are quieter.
    weights = weights * np.where(all_days.dayofweek >= 5, 0.7, 1.0)
    weights = weights / weights.sum()
    chosen = rng.choice(len(all_days), size=rows, p=weights)
    return pd.Series(all_days[chosen])


def generate(rows: int = DEFAULT_ROWS, seed: int = SEED) -> pd.DataFrame:
    """Build the sales DataFrame."""
    rng = np.random.default_rng(seed)

    region_names = list(REGIONS)
    region_weights = np.array([REGIONS[name][0] for name in region_names])
    region_weights = region_weights / region_weights.sum()
    regions = rng.choice(region_names, size=rows, p=region_weights)
    countries = np.array(
        [rng.choice(REGIONS[region][1]) for region in regions],
        dtype=object,
    )

    category_names = list(CATEGORIES)
    category_weights = np.array([CATEGORIES[name][0] for name in category_names])
    category_weights = category_weights / category_weights.sum()
    categories = rng.choice(category_names, size=rows, p=category_weights)
    products = np.array(
        [rng.choice(CATEGORIES[category][3]) for category in categories],
        dtype=object,
    )

    segments = rng.choice(SEGMENTS, size=rows, p=SEGMENT_WEIGHTS)
    channels = rng.choice(CHANNELS, size=rows, p=CHANNEL_WEIGHTS)
    dates = _weighted_dates(rng, rows)

    # Prices: lognormal around the category mean, so the distribution is skewed
    # like real prices are (a long right tail, no negative values).
    means = np.array([CATEGORIES[category][1] for category in categories])
    sigmas = np.array([CATEGORIES[category][2] for category in categories])
    unit_price = np.round(means * rng.lognormal(mean=0.0, sigma=sigmas, size=rows), 2)

    # Deliberate outliers: a pricing-feed glitch, which is exactly what
    # `detect_outliers` is meant to surface.
    outlier_positions = rng.choice(rows, size=OUTLIER_COUNT, replace=False)
    unit_price[outlier_positions] = np.round(
        unit_price[outlier_positions] * OUTLIER_FACTOR * rng.uniform(0.8, 1.4, OUTLIER_COUNT), 2
    )

    quantity = 1 + rng.poisson(lam=np.where(segments == "Enterprise", 6.0, 1.6), size=rows)

    discount_rate = np.array(
        [rng.choice(DISCOUNT_GRID, p=DISCOUNT_WEIGHTS[segment]) for segment in segments]
    )

    delivery_days = rng.poisson(
        lam=np.array([DELIVERY_MEAN[region] for region in regions]), size=rows
    ).astype(float)
    delivery_days[delivery_days == 0] = 1.0
    missing = rng.random(rows) < MISSING_DELIVERY_RATE
    delivery_days[missing] = np.nan

    revenue = np.round(quantity * unit_price * (1.0 - discount_rate), 2)

    frame = pd.DataFrame(
        {
            "order_id": [f"ORD-{100000 + index}" for index in range(rows)],
            "order_date": dates.dt.strftime("%Y-%m-%d"),
            "region": regions,
            "country": countries,
            "category": categories,
            "product": products,
            "channel": channels,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_rate": np.round(discount_rate, 2),
            "revenue": revenue,
            "customer_segment": segments,
            "delivery_days": delivery_days,
        }
    )
    return frame.sort_values("order_date", kind="stable").reset_index(drop=True)


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    frame = generate(rows=args.rows, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)

    print(f"{len(frame)} lignes écrites dans {args.output}")
    print(f"  chiffre d'affaires total : {frame['revenue'].sum():,.2f}")
    print(
        f"  par région : {frame.groupby('region')['revenue'].sum().sort_values(ascending=False).to_dict()}"
    )
    print(f"  delivery_days manquants : {frame['delivery_days'].isna().mean():.2%}")


if __name__ == "__main__":
    main()
