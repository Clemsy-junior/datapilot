"""In-process dataset registry.

Datasets are small enough (a few thousand rows) to live as pandas DataFrames in
the process, which keeps the tools synchronous, pure and trivially testable. The
store is the only component that knows about disk.

# TODO(R17): one dataset per question — no joins, no cross-dataset queries yet.
"""

from __future__ import annotations

import io
import re
import threading
import uuid
from pathlib import Path

import pandas as pd

from app.datasets.models import ColumnInfo, ColumnKind, DatasetInfo, DatasetSchema
from app.exceptions import DatasetNotFoundError, InvalidDatasetError
from app.serialization import frame_to_records, jsonify

#: Fraction of sampled values that must parse as dates before a text column is
#: treated as a datetime column. Below this we keep it categorical rather than
#: silently turning most of it into NaT.
_DATE_PARSE_THRESHOLD = 0.9

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Turn a file name into a short, URL-safe dataset id."""
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "dataset"


def _maybe_parse_dates(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns that are overwhelmingly ISO dates into datetimes.

    Done once at load time rather than inside every tool: `time_series` and
    `describe_column` both need real datetimes, and re-parsing per call would be
    both slow and a source of inconsistency between tools.
    """
    for column in frame.columns:
        series = frame[column]
        # pandas 2 reads text as `object`, pandas 3 as the dedicated `str` dtype:
        # accept both, and skip anything already typed (numeric, bool, datetime).
        is_text = pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
        if not is_text or pd.api.types.is_datetime64_any_dtype(series):
            continue
        sample = frame[column].dropna().head(200)
        if sample.empty:
            continue
        try:
            parsed = pd.to_datetime(sample, format="ISO8601", errors="coerce")
        except (ValueError, TypeError):
            continue
        if parsed.notna().mean() >= _DATE_PARSE_THRESHOLD:
            frame[column] = pd.to_datetime(frame[column], format="ISO8601", errors="coerce")
    return frame


def column_kind(series: pd.Series) -> ColumnKind:
    """Map a pandas dtype onto the four kinds the agent reasons about."""
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    return "categorical"


class LoadedDataset:
    """A DataFrame plus its metadata and its saved row selections."""

    def __init__(self, info: DatasetInfo, frame: pd.DataFrame) -> None:
        self.info = info
        self.frame = frame
        #: selection_id -> row index, produced by `filter_rows` so a later tool
        #: can re-use a filter without the LLM restating (or mistyping) it.
        self.selections: dict[str, pd.Index] = {}

    def describe(self) -> DatasetSchema:
        """Return columns and a 10-row preview."""
        columns = [
            ColumnInfo(
                name=str(name),
                kind=column_kind(self.frame[name]),
                dtype=str(self.frame[name].dtype),
                null_count=int(self.frame[name].isna().sum()),
                cardinality=int(self.frame[name].nunique(dropna=True)),
                sample_values=[jsonify(v) for v in self.frame[name].dropna().unique()[:5]],
            )
            for name in self.frame.columns
        ]
        return DatasetSchema(
            dataset=self.info,
            columns=columns,
            preview=frame_to_records(self.frame.head(10)),
        )


class DatasetStore:
    """Registry of the datasets this process can analyse.

    Thread-safe because uvicorn runs sync endpoint code in a worker thread pool;
    two concurrent uploads must not corrupt the registry.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._datasets: dict[str, LoadedDataset] = {}
        self._lock = threading.Lock()

    # --- loading -----------------------------------------------------------

    def discover(self) -> None:
        """Load every CSV bundled in the data directory.

        Called once at startup so the app is immediately usable: a cloned repo
        has `sales.csv` committed and needs no preparation step.
        """
        if not self._data_dir.is_dir():
            return
        for path in sorted(self._data_dir.glob("*.csv")):
            try:
                self._register(
                    dataset_id=_slugify(path.stem),
                    name=path.name,
                    frame=pd.read_csv(path),
                    source="bundled",
                    size_bytes=path.stat().st_size,
                )
            except (pd.errors.ParserError, UnicodeDecodeError, ValueError):
                # A malformed bundled file must not prevent the API from starting.
                continue

    def add_upload(self, filename: str, content: bytes) -> DatasetInfo:
        """Register an uploaded CSV and return its metadata."""
        if not content.strip():
            raise InvalidDatasetError("Le fichier envoyé est vide.")
        try:
            frame = pd.read_csv(io.BytesIO(content))
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
            raise InvalidDatasetError(
                "Ce fichier n'a pas pu être lu comme un CSV.",
                detail="Vérifiez le séparateur, l'encodage (UTF-8 attendu) et la ligne d'en-tête.",
            ) from exc
        if frame.empty or frame.columns.empty:
            raise InvalidDatasetError("Le CSV ne contient aucune donnée exploitable.")
        dataset_id = f"{_slugify(Path(filename).stem)}-{uuid.uuid4().hex[:6]}"
        return self._register(
            dataset_id=dataset_id,
            name=filename,
            frame=frame,
            source="upload",
            size_bytes=len(content),
        )

    def _register(
        self,
        *,
        dataset_id: str,
        name: str,
        frame: pd.DataFrame,
        source: str,
        size_bytes: int,
    ) -> DatasetInfo:
        frame = _maybe_parse_dates(frame.copy())
        info = DatasetInfo(
            id=dataset_id,
            name=name,
            row_count=len(frame),
            column_count=len(frame.columns),
            size_bytes=size_bytes,
            source=source,  # type: ignore[arg-type]
        )
        with self._lock:
            self._datasets[dataset_id] = LoadedDataset(info, frame)
        return info

    # --- reading -----------------------------------------------------------

    def list_datasets(self) -> list[DatasetInfo]:
        """Return every registered dataset, bundled ones first."""
        with self._lock:
            loaded = list(self._datasets.values())
        ordered = sorted(loaded, key=lambda d: (d.info.source != "bundled", d.info.name))
        return [d.info for d in ordered]

    def get(self, dataset_id: str) -> LoadedDataset:
        """Return a loaded dataset or raise `DatasetNotFoundError`."""
        with self._lock:
            dataset = self._datasets.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError(
                f"Le dataset « {dataset_id} » est introuvable.",
                detail="Utilisez GET /api/datasets pour lister les datasets disponibles.",
            )
        return dataset

    def frame(self, dataset_id: str) -> pd.DataFrame:
        """Return the DataFrame of a dataset."""
        return self.get(dataset_id).frame

    def schema(self, dataset_id: str) -> DatasetSchema:
        """Return columns and preview for a dataset."""
        return self.get(dataset_id).describe()

    # --- selections --------------------------------------------------------

    def save_selection(self, dataset_id: str, index: pd.Index) -> str:
        """Store a row selection and return the id the agent can reuse."""
        dataset = self.get(dataset_id)
        selection_id = f"sel_{uuid.uuid4().hex[:8]}"
        with self._lock:
            dataset.selections[selection_id] = index
        return selection_id

    def get_selection(self, dataset_id: str, selection_id: str) -> pd.Index | None:
        """Return a previously saved selection, or None if the id is unknown."""
        dataset = self.get(dataset_id)
        with self._lock:
            return dataset.selections.get(selection_id)
