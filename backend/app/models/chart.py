"""Chart specification.

The server never renders a picture. `make_chart` returns this declarative spec
and the React front end draws it with Recharts. Two reasons, both deliberate:
the payload stays small and inspectable (you can read the numbers a chart is
built from), and the chart inherits the browser's theme, resizing and tooltips
for free instead of being a dead PNG.

Mirror of `frontend/src/types/chart.ts` — keep both files in sync.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ChartType = Literal["bar", "line", "area", "scatter", "pie"]


class ChartSeries(BaseModel):
    """One drawn series, pointing at a key of every row in `ChartSpec.data`."""

    key: str = Field(description="Key to read in each data row.")
    label: str = Field(description="Human readable series name shown in the legend.")


class ChartSpec(BaseModel):
    """A chart the front end can render without asking the backend anything else."""

    type: ChartType
    title: str
    x_key: str = Field(description="Key of the categorical or temporal axis.")
    series: list[ChartSeries] = Field(min_length=1)
    data: list[dict[str, Any]] = Field(
        description="Rows, already aggregated and bounded by the tool that built them."
    )
    x_label: str | None = None
    y_label: str | None = None
    note: str | None = Field(
        default=None,
        description="Set when the underlying rows were truncated, so the UI can say so.",
    )

    @model_validator(mode="after")
    def _check_keys_exist(self) -> ChartSpec:
        """Fail fast if the spec references keys the rows do not carry.

        A chart that silently renders empty is much harder to debug than a spec
        rejected at construction time.
        """
        if not self.data:
            return self
        available = set(self.data[0])
        missing = [s.key for s in self.series if s.key not in available]
        if self.x_key not in available:
            missing.append(self.x_key)
        if missing:
            raise ValueError(
                f"chart references keys absent from the data rows: {sorted(set(missing))}"
            )
        return self
