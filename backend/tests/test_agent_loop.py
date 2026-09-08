"""Tests for the agent loop.

The loop is where the interesting failure modes live: a model that never stops
asking for tools, a tool that refuses, a provider that hangs. All three are
tested here against a scripted provider, which is the only way to make them
deterministic.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.loop import AgentRunner
from app.config import Settings
from app.conversations import ConversationStore
from app.exceptions import LLMError
from app.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.llm.fake import FakeLLMProvider


def tool_step(name: str, arguments: dict, call_id: str = "call_1") -> LLMResponse:
    """A scripted answer that asks for one tool."""
    return LLMResponse(
        content=[ToolUseBlock(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


def text_step(text: str) -> LLMResponse:
    """A scripted answer that ends the turn."""
    return LLMResponse(content=[TextBlock(text=text)], stop_reason="end_turn")


async def collect(runner: AgentRunner, conversation, message: str) -> list:
    """Drain the event generator into a list."""
    return [event async for event in runner.run(conversation=conversation, user_message=message)]


def kinds(events: list) -> list[str]:
    """The `type` of every event, in order."""
    return [event.type for event in events]


@pytest.fixture
def conversations() -> ConversationStore:
    return ConversationStore()


def build_runner(provider: LLMProvider, store, settings: Settings) -> AgentRunner:
    return AgentRunner(provider=provider, store=store, settings=settings)


class TestHappyPath:
    async def test_scripted_plan_produces_the_expected_calls(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(
            script=[
                tool_step("aggregate", {"group_by": ["region"], "agg": "sum", "metric": "revenue"}),
                text_step("APAC domine avec 5130."),
            ]
        )
        conversation = conversations.create("tiny")
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversation, "Quelle région performe le mieux ?")

        assert kinds(events) == [
            "status",
            "status",
            "tool_call",
            "tool_result",
            "status",
            "text_delta",
            "status",
            "done",
        ]
        tool_result = next(event for event in events if event.type == "tool_result")
        assert tool_result.ok is True
        assert tool_result.result["rows"][0]["region"] == "APAC"

        done = events[-1]
        assert done.stopped_reason == "completed"
        assert done.iterations == 2

    async def test_the_answer_is_appended_to_the_conversation(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(script=[text_step("Bonjour.")])
        conversation = conversations.create("tiny")
        runner = build_runner(provider, store, settings)

        await collect(runner, conversation, "Salut")

        assert [message.role for message in conversation.messages] == ["user", "assistant"]
        assert conversation.messages[-1].content == "Bonjour."

    async def test_text_deltas_reconstruct_the_answer_exactly(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        answer = "Le chiffre d'affaires de la région APAC atteint 5130 euros sur la période."
        provider = FakeLLMProvider(script=[text_step(answer)])
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversations.create("tiny"), "?")

        streamed = "".join(event.text for event in events if event.type == "text_delta")
        assert streamed == answer

    async def test_a_chart_is_emitted_once_and_not_sent_back_to_the_model(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(
            script=[
                tool_step(
                    "aggregate",
                    {"group_by": ["region"], "agg": "sum", "metric": "revenue"},
                    call_id="c1",
                ),
                tool_step(
                    "make_chart",
                    {
                        "result_id": "res_1",
                        "type": "bar",
                        "title": "CA par région",
                        "x_key": "region",
                        "series": [{"key": "value"}],
                    },
                    call_id="c2",
                ),
                text_step("Voici le graphique."),
            ]
        )
        conversation = conversations.create("tiny")
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversation, "Trace le CA par région")

        charts = [event for event in events if event.type == "chart"]
        assert len(charts) == 1
        assert charts[0].chart.x_key == "region"
        assert conversation.messages[-1].charts[0].type == "bar"

        # The rows travelled to the browser; the model only got the summary.
        last_call = provider.calls[-1]
        payloads = [
            json.loads(block.content)
            for message in last_call["messages"]
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        chart_payload = payloads[-1]
        assert "chart" not in chart_payload
        assert chart_payload["summary"]["points"] == 2


class TestGuardrails:
    async def test_iteration_limit_stops_the_loop_and_says_so(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        # A model that never stops asking for tools.
        provider = FakeLLMProvider(script=lambda _messages: tool_step("list_columns", {}))
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversations.create("tiny"), "Boucle sans fin")

        done = events[-1]
        assert done.stopped_reason == "max_iterations"
        assert done.iterations == settings.max_iterations
        assert len([event for event in events if event.type == "tool_call"]) == (
            settings.max_iterations
        )
        assert "limite" in "".join(event.text for event in events if event.type == "text_delta")

    async def test_a_tool_error_is_handed_back_to_the_model(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(
            script=[
                tool_step("describe_column", {"column": "inexistante"}),
                tool_step("describe_column", {"column": "revenue"}, call_id="call_2"),
                text_step("La colonne s'appelle revenue."),
            ]
        )
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversations.create("tiny"), "Décris la colonne")

        failed = [event for event in events if event.type == "tool_result" and not event.ok]
        assert len(failed) == 1
        assert "inexistante" in failed[0].error

        # The second LLM call must have seen the failure flagged as an error.
        second_call_blocks = [
            block
            for message in provider.calls[1]["messages"]
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        assert second_call_blocks[0].is_error is True
        assert events[-1].stopped_reason == "completed"

    async def test_the_global_timeout_stops_the_turn(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        class SlowProvider(LLMProvider):
            name = "slow"

            async def complete(self, **_kwargs) -> LLMResponse:
                await asyncio.sleep(5)
                return text_step("trop tard")

        impatient = settings.model_copy(update={"request_timeout_seconds": 0.05})
        runner = build_runner(SlowProvider(), store, impatient)

        events = await collect(runner, conversations.create("tiny"), "Question lente")

        assert events[-1].stopped_reason == "timeout"
        stopped = next(
            event for event in events if event.type == "status" and event.stage == "stopped"
        )
        assert "temps imparti" in stopped.message
        # The user still gets a message explaining what happened.
        assert "temps imparti" in conversations.get(events[-1].conversation_id).messages[-1].content

    async def test_a_provider_failure_becomes_an_error_event(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        class BrokenProvider(LLMProvider):
            name = "broken"

            async def complete(self, **_kwargs) -> LLMResponse:
                raise LLMError("Le service d'IA est injoignable.", detail="connexion refusée")

        runner = build_runner(BrokenProvider(), store, settings)

        events = await collect(runner, conversations.create("tiny"), "Question")

        assert kinds(events)[-2:] == ["error", "done"]
        assert events[-2].code == "llm_error"
        assert events[-1].stopped_reason == "error"

    async def test_tool_results_are_bounded_before_reaching_the_model(
        self, store, conversations: ConversationStore, settings: Settings
    ) -> None:
        tight = settings.model_copy(update={"max_tool_rows": 1})
        provider = FakeLLMProvider(
            script=[
                tool_step("aggregate", {"group_by": ["region"], "agg": "count"}),
                text_step("Fini."),
            ]
        )
        runner = build_runner(provider, store, tight)

        events = await collect(runner, conversations.create("tiny"), "Compte par région")

        result = next(event for event in events if event.type == "tool_result").result
        assert result["row_count"] == 1
        assert result["total_rows"] == 2
        assert result["truncated"] is True


class TestHistory:
    async def test_previous_turns_are_replayed_as_plain_text(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        conversation = conversations.create("tiny")
        first = FakeLLMProvider(script=[text_step("Première réponse.")])
        await collect(build_runner(first, store, settings), conversation, "Première question")

        second = FakeLLMProvider(script=[text_step("Deuxième réponse.")])
        await collect(build_runner(second, store, settings), conversation, "Deuxième question")

        sent: list[LLMMessage] = second.calls[0]["messages"]
        assert [message.role for message in sent] == ["user", "assistant", "user"]
        assert all(isinstance(block, TextBlock) for message in sent for block in message.content)

    async def test_the_system_prompt_carries_the_dataset_schema(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(script=[text_step("ok")])
        runner = build_runner(provider, store, settings)

        await collect(runner, conversations.create("tiny"), "Question")

        system = provider.calls[0]["system"]
        assert "tiny.csv" in system
        assert "region (categorical" in system
        assert "delivery_days" in system

    async def test_all_nine_tools_are_offered_to_the_model(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(script=[text_step("ok")])
        runner = build_runner(provider, store, settings)

        await collect(runner, conversations.create("tiny"), "Question")

        assert len(provider.calls[0]["tools"]) == 9


class TestScriptExhaustion:
    async def test_an_exhausted_script_is_a_readable_failure(
        self, store, settings: Settings, conversations: ConversationStore
    ) -> None:
        provider = FakeLLMProvider(script=[tool_step("list_columns", {})])
        runner = build_runner(provider, store, settings)

        events = await collect(runner, conversations.create("tiny"), "Question")

        assert events[-2].type == "error"
        assert "script" in (events[-2].detail or "").lower()
