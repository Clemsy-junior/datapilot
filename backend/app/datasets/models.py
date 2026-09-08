"""Dataset description models.

Mirror of `frontend/src/types/dataset.ts` — keep both files in sync.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Coarse column kind. The agent reasons on these three buckets rather than on
#: raw NumPy dtypes: it only ever needs to know whether it can average a column,
#: group by it, or resample it over time.
ColumnKind = Literal["numeric", "categorical", "datetime", "boolean"]


class ColumnInfo(BaseModel):
    """One column, as described to both the agent and the sidebar."""

    name: str
    kind: ColumnKind
    dtype: str = Field(description="Underlying pandas dtype, for the curious human.")
    null_count: int
    cardinality: int = Field(description="Number of distinct non-null values.")
    sample_values: list[Any] = Field(default_factory=list, max_length=5)


class DatasetInfo(BaseModel):
    """Identity and size of a loaded dataset."""

    id: str
    name: str
    row_count: int
    column_count: int
    size_bytes: int
    source: Literal["bundled", "upload"]


class DatasetSchema(BaseModel):
    """Columns plus a short preview, returned by `GET /api/datasets/{id}/schema`."""

    dataset: DatasetInfo
    columns: list[ColumnInfo]
    preview: list[dict[str, Any]] = Field(
        default_factory=list,
        description="At most 10 rows, JSON-safe, so the UI can show what the data looks like.",
    )
