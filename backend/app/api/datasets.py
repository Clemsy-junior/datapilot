"""Dataset endpoints: listing, upload, schema."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from app.agent.prompts import SUGGESTED_QUESTIONS
from app.api.deps import DatasetStoreDep, SettingsDep
from app.datasets.models import DatasetInfo, DatasetSchema
from app.exceptions import InvalidDatasetError

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetListResponse(BaseModel):
    """Payload of `GET /api/datasets`.

    The starter questions ride along on purpose: they depend on what the bundled
    dataset can actually answer, so the server is the right place to own them.
    """

    datasets: list[DatasetInfo]
    suggested_questions: list[str]


@router.get("", response_model=DatasetListResponse, summary="Lister les datasets chargés")
def list_datasets(store: DatasetStoreDep) -> DatasetListResponse:
    """Return every loaded dataset, bundled ones first."""
    return DatasetListResponse(
        datasets=store.list_datasets(),
        suggested_questions=SUGGESTED_QUESTIONS,
    )


@router.post("", response_model=DatasetInfo, status_code=201, summary="Uploader un CSV")
async def upload_dataset(
    store: DatasetStoreDep,
    settings: SettingsDep,
    file: UploadFile = File(description="Fichier CSV encodé en UTF-8."),
) -> DatasetInfo:
    """Register an uploaded CSV and return its metadata.

    The size limit is enforced on the bytes actually read rather than on the
    declared `Content-Length`, which a client controls and can lie about.
    """
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith((".csv", ".tsv", ".txt")):
        raise InvalidDatasetError(
            "Seuls les fichiers CSV sont acceptés.",
            detail=f"Fichier reçu : {filename}.",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise InvalidDatasetError(
            f"Le fichier dépasse la limite de {settings.max_upload_mb} Mo.",
            detail=f"Taille reçue : {len(content) / (1024 * 1024):.1f} Mo.",
        )
    return store.add_upload(filename, content)


@router.get(
    "/{dataset_id}/schema",
    response_model=DatasetSchema,
    summary="Colonnes, types et aperçu",
)
def dataset_schema(dataset_id: str, store: DatasetStoreDep) -> DatasetSchema:
    """Return the columns of a dataset plus a 10-row preview."""
    return store.schema(dataset_id)
