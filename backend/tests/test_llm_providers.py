"""Tests for the LLM abstraction, the fake provider and the Anthropic provider.

The Anthropic provider is tested against an `httpx.MockTransport`: no key, no
network, but the real request-building and response-decoding code runs.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.exceptions import LLMError
from app.llm import build_provider
from app.llm.anthropic import AnthropicProvider
from app.llm.base import (
    LLMMessage,
    LLMResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.llm.fake import FakeLLMProvider

TOOLS = [{"name": "list_columns", "description": "…", "input_schema": {"type": "object"}}]


class TestResponseHelpers:
    def test_text_and_tool_uses_are_separated(self) -> None:
        response = LLMResponse(
            content=[
                TextBlock(text="Je regarde "),
                TextBlock(text="les colonnes."),
                ToolUseBlock(id="c1", name="list_columns", arguments={}),
            ]
        )

        assert response.text == "Je regarde les colonnes."
        assert [block.name for block in response.tool_uses] == ["list_columns"]


class TestFakeProvider:
    async def test_scripted_mode_returns_steps_in_order(self) -> None:
        provider = FakeLLMProvider(
            script=[
                LLMResponse(content=[TextBlock(text="un")]),
                LLMResponse(content=[TextBlock(text="deux")]),
            ]
        )

        first = await provider.complete(system="", messages=[], tools=[], max_tokens=10)
        second = await provider.complete(system="", messages=[], tools=[], max_tokens=10)

        assert (first.text, second.text) == ("un", "deux")
        assert len(provider.calls) == 2

    async def test_an_exhausted_script_raises_a_readable_error(self) -> None:
        provider = FakeLLMProvider(script=[])

        with pytest.raises(LLMError, match="scripté"):
            await provider.complete(system="", messages=[], tools=[], max_tokens=10)

    async def test_heuristic_mode_starts_by_listing_columns(self) -> None:
        provider = FakeLLMProvider()

        response = await provider.complete(
            system="",
            messages=[LLMMessage.user_text("Quelle région performe le mieux ?")],
            tools=TOOLS,
            max_tokens=10,
        )

        assert [block.name for block in response.tool_uses] == ["list_columns"]

    async def test_heuristic_mode_picks_the_column_the_question_names(self) -> None:
        provider = FakeLLMProvider()
        schema = {
            "columns": [
                {"name": "region", "kind": "categorical", "cardinality": 4},
                {"name": "category", "kind": "categorical", "cardinality": 5},
                {"name": "revenue", "kind": "numeric", "cardinality": 900},
            ]
        }
        messages = [
            LLMMessage.user_text("Quelle région performe le mieux ?"),
            LLMMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="c1", content=json.dumps(schema))],
            ),
        ]

        response = await provider.complete(system="", messages=messages, tools=TOOLS, max_tokens=10)

        call = response.tool_uses[0]
        assert call.name == "aggregate"
        # « région » must win over « category » despite the accent.
        assert call.arguments["group_by"] == ["region"]
        assert call.arguments["metric"] == "revenue"

    async def test_callable_scripts_are_supported(self) -> None:
        provider = FakeLLMProvider(
            script=lambda messages: LLMResponse(
                content=[TextBlock(text=f"{len(messages)} message(s)")]
            )
        )

        response = await provider.complete(
            system="", messages=[LLMMessage.user_text("a")], tools=[], max_tokens=10
        )

        assert response.text == "1 message(s)"


class TestAnthropicProvider:
    def _provider(self, handler) -> AnthropicProvider:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, base_url="https://api.test")
        return AnthropicProvider(api_key="sk-test", model="test-model", client=client)

    async def test_request_shape_and_response_decoding(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "text", "text": "Je vais regarder."},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "aggregate",
                            "input": {"group_by": ["region"]},
                        },
                        {"type": "thinking", "thinking": "ignoré"},
                    ],
                    "stop_reason": "tool_use",
                },
            )

        provider = self._provider(handler)
        messages = [
            LLMMessage.user_text("Question"),
            LLMMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_0", name="list_columns", arguments={})],
            ),
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="toolu_0", content="{}", is_error=True),
                ],
            ),
        ]

        response = await provider.complete(
            system="Tu es DataPilot.", messages=messages, tools=TOOLS, max_tokens=512
        )

        assert captured["url"].endswith("/v1/messages")
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["system"] == "Tu es DataPilot."
        assert captured["body"]["tools"] == TOOLS
        assert captured["body"]["messages"][1]["content"][0]["type"] == "tool_use"
        assert captured["body"]["messages"][2]["content"][0]["is_error"] is True

        assert response.text == "Je vais regarder."
        assert response.stop_reason == "tool_use"
        assert [block.name for block in response.tool_uses] == ["aggregate"]
        # Unknown block types are dropped rather than crashing the loop.
        assert len(response.content) == 2

        await provider.aclose()

    async def test_http_error_is_wrapped_without_leaking_the_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": {"type": "rate_limit_error", "message": "slow down"}}
            )

        provider = self._provider(handler)

        with pytest.raises(LLMError) as excinfo:
            await provider.complete(system="", messages=[], tools=[], max_tokens=10)

        assert excinfo.value.code == "llm_error"
        assert "429" in (excinfo.value.detail or "")
        assert "slow down" in (excinfo.value.detail or "")

    async def test_transport_failure_is_wrapped(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = self._provider(handler)

        with pytest.raises(LLMError, match="injoignable"):
            await provider.complete(system="", messages=[], tools=[], max_tokens=10)

    def test_a_missing_key_is_refused_at_construction(self) -> None:
        with pytest.raises(LLMError, match="clé API"):
            AnthropicProvider(api_key="", model="test-model")


class TestProviderFactory:
    def test_defaults_to_the_fake_provider(self) -> None:
        provider = build_provider(Settings(llm_provider="fake"))

        assert provider.name == "fake"

    def test_builds_the_anthropic_provider_when_configured(self) -> None:
        provider = build_provider(Settings(llm_provider="anthropic", anthropic_api_key="sk-test"))

        assert provider.name == "anthropic"

    def test_anthropic_without_a_key_fails_loudly(self) -> None:
        with pytest.raises(LLMError):
            build_provider(Settings(llm_provider="anthropic", anthropic_api_key=""))
