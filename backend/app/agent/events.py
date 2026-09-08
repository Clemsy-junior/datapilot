"""Server-Sent Events emitted while the agent works.

One event type per thing the user can see happening. The front end renders them
incrementally, which is what turns an opaque "thinking…" spinner into the
AgentTrace timeline — the whole point of the project's UI.

Mirror of `frontend/src/types/events.ts` — keep both files in sync.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from app.models.chart import ChartSpec


class StatusEvent(BaseModel):
    """Coarse progress marker: the loop started, is calling the model, is done."""

    type: Literal["status"] = "status"
    stage: Literal["started", "thinking", "tool", "answering", "stopped"]
    message: str
    iteration: int = 0


class ToolCallEvent(BaseModel):
    """The model asked for a tool. Emitted *before* the tool runs."""

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]
    iteration: int


class ToolResultEvent(BaseModel):
    """The tool answered — or refused. `ok=False` carries the message the model sees."""

    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    ok: bool
    duration_ms: float
    result: dict[str, Any] | None = None
    error: str | None = None


class TextDeltaEvent(BaseModel):
    """A chunk of the final answer.

    # TODO(R07): chunks are sliced from the completed answer, not streamed from
    # the provider. Real token streaming replaces this without touching the
    # front end, because the event contract stays identical.
    """

    type: Literal["text_delta"] = "text_delta"
    text: str


class ChartEvent(BaseModel):
    """A chart the agent built, ready to render."""

    type: Literal["chart"] = "chart"
    chart: ChartSpec


class ErrorEvent(BaseModel):
    """Something went wrong; the stream ends after this."""

    type: Literal["error"] = "error"
    code: str
    message: str
    detail: str | None = None


class DoneEvent(BaseModel):
    """Terminal event, always sent — including after an error — so clients can close."""

    type: Literal["done"] = "done"
    conversation_id: str
    message_id: str
    stopped_reason: Literal["completed", "max_iterations", "timeout", "error"] = "completed"
    iterations: int = 0


AgentEvent = Annotated[
    Union[  # noqa: UP007 - Annotated discriminated unions need the explicit form
        StatusEvent,
        ToolCallEvent,
        ToolResultEvent,
        TextDeltaEvent,
        ChartEvent,
        ErrorEvent,
        DoneEvent,
    ],
    Field(discriminator="type"),
]

agent_event_adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


def format_sse(event: BaseModel) -> str:
    """Serialise an event in the `text/event-stream` wire format.

    The event name is duplicated in the JSON payload on purpose: browsers can
    subscribe per event name via `addEventListener`, while clients that read the
    generic `message` stream still know what they received.
    """
    payload = event.model_dump(mode="json")
    name = payload["type"]
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n"


__all__ = [
    "AgentEvent",
    "ChartEvent",
    "DoneEvent",
    "ErrorEvent",
    "StatusEvent",
    "TextDeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "agent_event_adapter",
    "format_sse",
]
