"""Health endpoint.

Kept trivially cheap and dependency-free: Docker Compose gates the frontend on
this check, so it must answer before the dataset store has done anything
interesting, and it must never touch the network.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import DatasetStoreDep, SettingsDep
from app.config import VERSION
from app.tools.registry import registry

# TODO(R05): no metrics endpoint yet — /metrics and Prometheus counters
# (requests, tool calls, agent iterations, LLM latency) belong next to this one.
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Payload of `GET /api/health`."""

    status: Literal["ok"] = "ok"
    version: str
    llm_provider: str
    dataset_count: int
    tool_count: int


@router.get("/health", response_model=HealthResponse, summary="Liveness et configuration")
def health(settings: SettingsDep, store: DatasetStoreDep) -> HealthResponse:
    """Report liveness plus the few facts a demo needs to prove it runs offline."""
    return HealthResponse(
        version=VERSION,
        llm_provider=settings.llm_provider,
        dataset_count=len(store.list_datasets()),
        tool_count=len(registry.names()),
    )
