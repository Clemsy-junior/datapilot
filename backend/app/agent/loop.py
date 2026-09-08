"""The agentic loop.

    message utilisateur
          │
          ▼
    ┌──────────────────────────────────────────────┐
    │ appel LLM (system + schémas d'outils          │
    │            + historique + résultats d'outils) │
    └──────────────────────────────────────────────┘
          │
          ├── demande d'outil ──► exécution Python déterministe ──┐
          │                                                        │
          │  ◄─────────────────────────────────────────────────────┘
          │
          └── texte final ──► réponse à l'utilisateur

One implementation serves both HTTP endpoints: `run()` is an async generator of
events. `/api/chat/stream` forwards them as SSE; `/api/chat` drains the same
generator and returns the assembled message. There is no second code path that
could behave differently from the one the browser exercises.

Three guardrails, all configurable and all tested:

* `max_iterations` — a model that keeps asking for tools is stopped and says so;
* `request_timeout_seconds` — a global deadline covering LLM calls and tools;
* bounded tool results — enforced in the tools themselves (`bound_rows`).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from app.agent.events import (
    ChartEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agent.prompts import build_system_prompt
from app.agent.schemas import tool_schemas
from app.config import Settings
from app.datasets.store import DatasetStore
from app.exceptions import DataPilotError, LLMError, ToolError
from app.llm.base import (
    LLMMessage,
    LLMProvider,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.models.chart import ChartSpec
from app.models.chat import ChatMessage, Conversation, ToolInvocation
from app.tools.registry import ToolContext, ToolRegistry
from app.tools.registry import registry as default_registry

#: Size of the slices the final answer is cut into for `text_delta` events.
#: Small enough to look alive, large enough not to flood the event stream.
_CHUNK_SIZE = 48


class AgentRunner:
    """Runs one user message through the LLM/tool loop, emitting events as it goes."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        store: DatasetStore,
        settings: Settings,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._settings = settings
        self._registry = registry or default_registry

    async def run(
        self, *, conversation: Conversation, user_message: str
    ) -> AsyncIterator[BaseModel]:
        """Yield the events of one agent turn and append the answer to `conversation`."""
        deadline = time.monotonic() + self._settings.request_timeout_seconds
        schema = self._store.schema(conversation.dataset_id)
        system = build_system_prompt(schema, self._settings.max_iterations)
        tools = tool_schemas(self._registry)

        conversation.messages.append(
            ChatMessage(id=_new_id("msg"), role="user", content=user_message)
        )
        llm_messages = _history_to_llm_messages(conversation)

        context = ToolContext(
            store=self._store,
            dataset_id=conversation.dataset_id,
            max_rows=self._settings.max_tool_rows,
        )
        invocations: list[ToolInvocation] = []
        charts: list[ChartSpec] = []
        answer_parts: list[str] = []
        stopped_reason = "max_iterations"
        iteration = 0

        yield StatusEvent(stage="started", message="Analyse de la question…", iteration=0)

        try:
            for iteration in range(1, self._settings.max_iterations + 1):
                _check_deadline(deadline)
                yield StatusEvent(
                    stage="thinking",
                    message="Le modèle choisit les outils à appeler…",
                    iteration=iteration,
                )

                response = await asyncio.wait_for(
                    self._provider.complete(
                        system=system,
                        messages=llm_messages,
                        tools=tools,
                        max_tokens=self._settings.llm_max_tokens,
                    ),
                    timeout=max(0.01, deadline - time.monotonic()),
                )

                if response.text:
                    answer_parts.append(response.text)
                    for chunk in _chunks(response.text):
                        yield TextDeltaEvent(text=chunk)

                tool_uses = response.tool_uses
                if not tool_uses:
                    stopped_reason = "completed"
                    break

                llm_messages.append(LLMMessage(role="assistant", content=list(response.content)))
                result_blocks: list[ToolResultBlock] = []

                for tool_use in tool_uses:
                    yield ToolCallEvent(
                        id=tool_use.id,
                        name=tool_use.name,
                        arguments=tool_use.arguments,
                        iteration=iteration,
                    )
                    _check_deadline(deadline)
                    event, block, chart = await self._execute(tool_use, context)
                    invocations.append(
                        ToolInvocation(
                            id=tool_use.id,
                            name=tool_use.name,
                            arguments=tool_use.arguments,
                            duration_ms=event.duration_ms,
                            ok=event.ok,
                            result=event.result,
                            error=event.error,
                        )
                    )
                    yield event
                    if chart is not None:
                        charts.append(chart)
                        yield ChartEvent(chart=chart)
                    result_blocks.append(block)

                llm_messages.append(LLMMessage(role="user", content=list(result_blocks)))
            else:
                # The `for` finished without `break`: the model never stopped
                # asking for tools. Say so instead of pretending to answer.
                note = (
                    f"J'ai atteint la limite de {self._settings.max_iterations} appels d'outils "
                    "sans parvenir à conclure. Voici ce que j'ai pu établir ; reformulez la "
                    "question de façon plus ciblée pour aller plus loin."
                )
                answer_parts.append(note)
                for chunk in _chunks(note):
                    yield TextDeltaEvent(text=chunk)

        except TimeoutError:
            stopped_reason = "timeout"
            note = (
                f"Le temps imparti ({self._settings.request_timeout_seconds:.0f} s) est écoulé "
                "avant la fin de l'analyse. Essayez une question plus précise."
            )
            answer_parts.append(note)
            yield StatusEvent(stage="stopped", message=note, iteration=iteration)

        except LLMError as exc:
            yield ErrorEvent(code=exc.code, message=exc.message, detail=exc.detail)
            yield DoneEvent(
                conversation_id=conversation.id,
                message_id="",
                stopped_reason="error",
                iterations=iteration,
            )
            return

        except DataPilotError as exc:  # pragma: no cover - defensive
            yield ErrorEvent(code=exc.code, message=exc.message, detail=exc.detail)
            yield DoneEvent(
                conversation_id=conversation.id,
                message_id="",
                stopped_reason="error",
                iterations=iteration,
            )
            return

        message = ChatMessage(
            id=_new_id("msg"),
            role="assistant",
            content="\n\n".join(part for part in answer_parts if part).strip(),
            tool_invocations=invocations,
            charts=charts,
        )
        conversation.messages.append(message)

        yield StatusEvent(stage="answering", message="Réponse prête.", iteration=iteration)
        yield DoneEvent(
            conversation_id=conversation.id,
            message_id=message.id,
            stopped_reason=stopped_reason,  # type: ignore[arg-type]
            iterations=iteration,
        )

    async def _execute(
        self, tool_use: ToolUseBlock, context: ToolContext
    ) -> tuple[ToolResultEvent, ToolResultBlock, ChartSpec | None]:
        """Run one tool and package what the UI, the model and the trace each need.

        A `ToolError` is *not* propagated: it becomes a tool result flagged as an
        error, which the model reads on the next iteration and can recover from.
        """
        try:
            # pandas is synchronous and CPU-bound; off-loading it keeps the event
            # loop free to flush SSE frames to the browser while it runs.
            result, duration_ms = await asyncio.to_thread(
                self._registry.dispatch, tool_use.name, tool_use.arguments, context
            )
        except ToolError as exc:
            message = exc.message if not exc.detail else f"{exc.message} {exc.detail}"
            event = ToolResultEvent(
                id=tool_use.id,
                name=tool_use.name,
                ok=False,
                duration_ms=0.0,
                error=message,
            )
            block = ToolResultBlock(
                tool_use_id=tool_use.id,
                content=json.dumps({"error": message}, ensure_ascii=False),
                is_error=True,
            )
            return event, block, None

        chart: ChartSpec | None = None
        model_payload: dict[str, Any] = result
        if "chart" in result:
            chart = ChartSpec.model_validate(result["chart"])
            # The chart data already travelled to the browser; sending it back to
            # the model too would duplicate it in the context for nothing.
            model_payload = {key: value for key, value in result.items() if key != "chart"}

        event = ToolResultEvent(
            id=tool_use.id,
            name=tool_use.name,
            ok=True,
            duration_ms=round(duration_ms, 3),
            result=model_payload,
        )
        block = ToolResultBlock(
            tool_use_id=tool_use.id,
            content=json.dumps(model_payload, ensure_ascii=False, default=str),
        )
        return event, block, chart


def _history_to_llm_messages(conversation: Conversation) -> list[LLMMessage]:
    """Rebuild the provider-facing history from the stored conversation.

    Only the plain text of previous turns is replayed: their tool calls and
    results are dropped. That keeps the context small and predictable, at the
    cost of the model not remembering *how* it answered before — an acceptable
    trade for a single-dataset assistant, and the reason `filter_rows` hands out
    reusable selection ids instead.
    """
    messages: list[LLMMessage] = []
    for message in conversation.messages:
        if not message.content.strip():
            continue
        messages.append(LLMMessage(role=message.role, content=[TextBlock(text=message.content)]))
    if not messages or messages[-1].role != "user":  # pragma: no cover - defensive
        messages.append(LLMMessage.user_text("Continue."))
    return messages


def _check_deadline(deadline: float) -> None:
    """Raise `TimeoutError` once the global budget for this turn is spent."""
    if time.monotonic() >= deadline:
        raise TimeoutError


def _chunks(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into delta-sized pieces without cutting inside a word."""
    words = text.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if len(candidate) >= size:
            chunks.append(candidate)
            current = ""
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk if index == 0 else f" {chunk}" for index, chunk in enumerate(chunks)]


def _new_id(prefix: str) -> str:
    """Short, collision-free identifier for messages and conversations."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
