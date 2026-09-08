"""Shared fixtures.

The unit tests deliberately do **not** use the bundled 5 000-row dataset: they
run against a six-row CSV whose every aggregate can be computed by hand, so an
assertion failure points at a bug rather than at a data change. The bundled
dataset is exercised separately, in `test_dataset_generation.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.datasets.store import DatasetStore
from app.llm.fake import FakeLLMProvider
from app.main import create_app
from app.tools.registry import ToolContext

#: Six orders, three months, two regions, two categories.
#: revenue by region : EMEA 220.0, APAC 5130.0
#: revenue by category : Tech 5200.0, Home 150.0
#: revenue by month : 2024-01 120.0, 2024-02 5030.0, 2024-03 200.0
#: `tax_rate` is constant on purpose, to exercise the divide-by-zero guards.
#: `delivery_days` has one missing value, to exercise null handling.
TINY_CSV = """\
order_id,order_date,region,category,quantity,unit_price,revenue,tax_rate,delivery_days
O1,2024-01-05,EMEA,Home,2,10.0,20.0,0.2,3
O2,2024-01-20,EMEA,Tech,1,100.0,100.0,0.2,5
O3,2024-02-03,APAC,Home,3,10.0,30.0,0.2,
O4,2024-02-15,APAC,Tech,1,5000.0,5000.0,0.2,9
O5,2024-03-01,EMEA,Home,4,25.0,100.0,0.2,2
O6,2024-03-11,APAC,Tech,2,50.0,100.0,0.2,4
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A data directory containing only the tiny dataset."""
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "tiny.csv").write_text(TINY_CSV, encoding="utf-8")
    return directory


@pytest.fixture
def store(data_dir: Path) -> DatasetStore:
    """A dataset store with `tiny` loaded."""
    store = DatasetStore(data_dir)
    store.discover()
    return store


@pytest.fixture
def context(store: DatasetStore) -> ToolContext:
    """A tool context pointing at the tiny dataset, with a small row budget."""
    return ToolContext(store=store, dataset_id="tiny", max_rows=50)


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    """Settings isolated from the environment and from the repo's real data."""
    return Settings(
        llm_provider="fake",
        data_dir=data_dir,
        max_iterations=4,
        request_timeout_seconds=10.0,
        max_tool_rows=50,
        cors_origins=["http://localhost:5173"],
    )


@pytest.fixture
def provider() -> FakeLLMProvider:
    """The heuristic fake provider (no script)."""
    return FakeLLMProvider()


@pytest.fixture
def client(settings: Settings, provider: FakeLLMProvider) -> Iterator[TestClient]:
    """A TestClient over an app wired to the tiny dataset and the fake provider."""
    app = create_app(settings=settings, provider=provider)
    with TestClient(app) as test_client:
        yield test_client
