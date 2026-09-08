"""Provider-agnostic LLM interface.

The agent loop talks only to this abstraction. Everything vendor-specific — HTTP
shape, block names, authentication — lives in a provider implementation, which
is what lets the whole test suite run against `FakeLLMProvider` with no API key
and no network.

The vocabulary (text blocks, tool-use blocks, tool-result blocks) follows the
tool-calling model that every current provider converged on, so a new provider
is a translation layer rather than a redesign.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextBlock:
    """Free text produced by the model."""

    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """The model's request to run a tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    """What we send back after running a tool.

    `is_error=True` is a normal, expected value: a failed tool is information the
    model is meant to act on, not a reason to abort the conversation.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class LLMMessage:
    """One turn of the conversation as the provider sees it."""

    role: Literal["user", "assistant"]
    content: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def user_text(cls, text: str) -> LLMMessage:
        """Build a plain user message."""
        return cls(role="user", content=[TextBlock(text=text)])


@dataclass
class LLMResponse:
    """One model answer: some text, some tool requests, or both."""

    content: list[ContentBlock]
    stop_reason: str = "end_turn"

    @property
    def text(self) -> str:
        """Concatenated text blocks."""
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """Tool requests, in the order the model emitted them."""
        return [block for block in self.content if isinstance(block, ToolUseBlock)]


class LLMProvider(ABC):
    """What the agent loop needs from a language model."""

    #: Short identifier surfaced in `/api/health`, so a demo can prove at a
    #: glance whether it is running offline or against a real API.
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """Return the model's next answer given the conversation and the tool schemas."""

    async def aclose(self) -> None:
        """Release provider resources. No-op unless the provider holds a client."""
        return None
