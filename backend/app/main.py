"""FastAPI application: wiring, middleware and error handling.

Everything the process needs is built once in the lifespan handler and hung on
`app.state`. `create_app()` takes optional overrides so tests can build a fully
configured application — with their own dataset directory and their own LLM
provider — without touching the environment or the filesystem of the repo.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import chat, datasets, health
from app.config import VERSION, Settings, get_settings
from app.conversations import ConversationStore
from app.datasets.store import DatasetStore
from app.exceptions import DataPilotError
from app.llm import build_provider
from app.llm.base import LLMProvider
from app.models.errors import ErrorBody, ErrorEnvelope

# TODO(R04): plain text logs — replaced by structured JSON logs with a
# correlation id per request.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("datapilot")

DESCRIPTION = """\
DataPilot — un agent qui analyse un jeu de données tabulaire.

Le modèle orchestre, il ne calcule jamais : tous les chiffres renvoyés proviennent
d'outils Python déterministes (pandas), dont les résultats sont bornés avant d'être
transmis au modèle.
"""


def _error_response(status_code: int, code: str, message: str, detail: str | None) -> JSONResponse:
    """Render the one error shape the whole API uses."""
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message, detail=detail))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def create_app(
    *,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    dataset_store: DatasetStore | None = None,
) -> FastAPI:
    """Build the application. Overrides exist for tests; production passes none."""
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = dataset_store or DatasetStore(resolved_settings.resolved_data_dir)
        if dataset_store is None:
            store.discover()
        application.state.settings = resolved_settings
        application.state.dataset_store = store
        application.state.conversation_store = ConversationStore()
        application.state.llm_provider = provider or build_provider(resolved_settings)
        logger.info(
            "datapilot ready — provider=%s datasets=%s",
            application.state.llm_provider.name,
            len(store.list_datasets()),
        )
        try:
            yield
        finally:
            await application.state.llm_provider.aclose()

    application = FastAPI(
        title="DataPilot API",
        description=DESCRIPTION,
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # TODO(R15): CORS is permissive on methods and headers for the dev origins;
    # hardening it goes with the API-key authentication step.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    application.include_router(health.router, prefix="/api")
    application.include_router(datasets.router, prefix="/api")
    application.include_router(chat.router, prefix="/api")

    @application.exception_handler(DataPilotError)
    async def _handle_app_error(_: Request, exc: DataPilotError) -> JSONResponse:
        """Turn a deliberate application error into the structured envelope."""
        return _error_response(exc.status_code, exc.code, exc.message, exc.detail)

    @application.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Report malformed requests in the same envelope, in plain language."""
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", [])[1:]) or "requête"
        return _error_response(
            422,
            "invalid_request",
            "La requête est mal formée.",
            f"{location} : {first.get('msg', 'valeur invalide')}",
        )

    @application.exception_handler(StarletteHTTPException)
    async def _handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Keep framework-level errors (404 on an unknown path) in the same shape."""
        return _error_response(exc.status_code, f"http_{exc.status_code}", str(exc.detail), None)

    @application.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        """Log the stack trace server-side and return nothing revealing to the client."""
        logger.exception("unhandled error", exc_info=exc)
        return _error_response(
            500,
            "internal_error",
            "Une erreur interne est survenue.",
            "L'incident a été enregistré côté serveur.",
        )

    return application


app = create_app()
