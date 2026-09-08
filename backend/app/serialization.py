"""Conversion of pandas values to JSON-safe Python.

`json.dumps` chokes on NumPy scalars, `NaT`, `NaN` and `Timestamp`, and every
one of those appears in real tool output. Everything that leaves a tool goes
through `jsonify` so a single place decides how they are represented — notably
that `NaN` becomes `null` rather than the literal `NaN`, which is invalid JSON
and which browsers reject.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd


def jsonify(value: Any) -> Any:
    """Return `value` converted to a JSON-serialisable Python object."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float | np.floating):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, bool | np.bool_):
        return bool(value)
    if isinstance(value, int | np.integer):
        return int(value)
    if isinstance(value, pd.Timestamp | dt.datetime):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return [jsonify(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(key): jsonify(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonify(item) for item in value]
    if isinstance(value, str):
        return value
    # pandas nullable scalars (pd.NA) reach here.
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a DataFrame to JSON-safe row dictionaries, column order preserved."""
    columns = list(frame.columns)
    return [
        {str(column): jsonify(value) for column, value in zip(columns, row, strict=True)}
        for row in frame.itertuples(index=False, name=None)
    ]
