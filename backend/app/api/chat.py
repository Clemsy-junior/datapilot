"""Chat endpoints: the buffered one and the streamed one.

Both drive the *same* `AgentRunner.run()` generator. `/api/chat` drains it and
returns the finished message — which is what makes integration tests short and
deterministic. `/api/chat/stream` forwards each event as SSE, which is what the
browser uses.

`/api/chat/stream` is a GET with query parameters because that is the only shape
the browser's `EventSource` supports. The trade-off (the question ends up in the
URL, and therefore in access logs) is discussed in ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.agent.events import ErrorEvent, format_sse
from app.api.deps import AgentRunnerDep, ConversationStoreDep, DatasetStoreDep
from app.exceptions import DataPilotError
from app.models.chat import ChatRequest, ChatResponse, Conversation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

#: Headers that keep an SSE stream flowing end to end. `X-Accel-Buffering: no`
#: is the one that matters in this project: without it nginx buffers the
#: response and the AgentTrace only appears once the agent has finished, which
#: defeats the point of streaming at all.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# TODO(R06): no rate limiting — one client can open as many agent runs as it
# likes, and each one costs LLM tokens.
@router.post("/chat", response_model=ChatResponse, summary="Exécuter la boucle agent")
async def chat(
    payload: ChatRequest,
    runner: AgentRunnerDep,
    conversations: ConversationStoreDep,
    store: DatasetStoreDep,
) -> ChatResponse:
    """Run the agent to completion and return the assistant message."""
    store.get(payload.dataset_id)  # 404 early rather than mid-run
    conversation = conversations.get_or_create(payload.conversation_id, payload.dataset_id)

    stopped_reason = "completed"
    async for event in runner.run(conversation=conversation, user_message=payload.message):
        kind = getattr(event, "type", None)
        if kind == "error":
            raise DataPilotError(event.message, event.detail)  # type: ignore[attr-defined]
        if kind == "done":
            stopped_reason = event.stopped_reason  # type: ignore[attr-defined]

    return ChatResponse(
        conversation_id=conversation.id,
        message=conversation.messages[-1],
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )


@router.get("/chat/stream", summary="Exécuter la boucle agent en Server-Sent Events")
async def chat_stream(
    runner: AgentRunnerDep,
    conversations: ConversationStoreDep,
    store: DatasetStoreDep,
    message: str = Query(min_length=1, max_length=4000),
    dataset_id: str = Query(min_length=1),
    conversation_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream the agent's progress: status, tool_call, tool_result, text_delta, chart, done."""
    # Resolve both ids *before* the stream starts, so a bad id is a plain JSON
    # 404 the browser can read rather than an error buried inside the stream.
    store.get(dataset_id)
    conversation = conversations.get_or_create(conversation_id, dataset_id)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in runner.run(conversation=conversation, user_message=message):
                yield format_sse(event)
        except DataPilotError as exc:  # pragma: no cover - defensive
            yield format_sse(ErrorEvent(code=exc.code, message=exc.message, detail=exc.detail))
        except Exception:  # pragma: no cover - defensive
            logger.exception("agent stream failed")
            yield format_sse(
                ErrorEvent(
                    code="internal_error",
                    message="Une erreur interne a interrompu l'analyse.",
                )
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# TODO(R10): no Markdown export of a conversation yet.
@router.get(
    "/conversations/{conversation_id}",
    response_model=Conversation,
    summary="Historique d'une conversation",
)
def get_conversation(conversation_id: str, conversations: ConversationStoreDep) -> Conversation:
    """Return the full history of a conversation."""
    return conversations.get(conversation_id)
