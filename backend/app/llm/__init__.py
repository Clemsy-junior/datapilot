"""LLM providers and the factory that picks one from configuration."""

from __future__ import annotations

from app.config import Settings
from app.llm.anthropic import AnthropicProvider
from app.llm.base import (
    ContentBlock,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.llm.fake import FakeLLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    """Instantiate the provider named by `DATAPILOT_LLM_PROVIDER`.

    Defaults to `fake` so a fresh clone runs, demos and passes CI without a key.
    """
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            timeout=settings.request_timeout_seconds,
        )
    return FakeLLMProvider()


__all__ = [
    "AnthropicProvider",
    "ContentBlock",
    "FakeLLMProvider",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "TextBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "build_provider",
]
