"""Conversation and chat transport models.

Mirror of `frontend/src/types/chat.ts` — keep both files in sync.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.chart import ChartSpec


def _now() -> datetime:
    """Timezone-aware creation timestamp (naive datetimes bite across containers)."""
    return datetime.now(UTC)


class ToolInvocation(BaseModel):
    """One tool call the agent made, as shown in the AgentTrace timeline.

    This is the audit trail that backs the project's core claim: every figure in
    an answer can be traced to a deterministic Python call with visible inputs.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    duration_ms: float
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ChatMessage(BaseModel):
    """A message in a conversation as the front end displays it."""

    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=_now)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """Body of `POST /api/chat`."""

    message: str = Field(min_length=1, max_length=4000)
    dataset_id: str
    conversation_id: str | None = Field(
        default=None,
        description="Omit to start a new conversation; the id is returned in the response.",
    )


class ChatResponse(BaseModel):
    """Body of `POST /api/chat` — the whole agent run, already finished."""

    conversation_id: str
    message: ChatMessage
    stopped_reason: Literal["completed", "max_iterations", "timeout"] = "completed"


class Conversation(BaseModel):
    """Full history of one conversation.

    # TODO(R01): kept in memory only — moves to SQLite + SQLAlchemy.
    """

    id: str
    dataset_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
