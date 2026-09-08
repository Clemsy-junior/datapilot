"""Anthropic Messages API provider (tool use).

Written directly against the HTTP API with `httpx` rather than a vendor SDK, for
two reasons that are worth defending in an interview: the wire format *is* the
contract this project teaches, and one fewer dependency means one fewer
transitive upgrade to babysit.

Only the non-streaming endpoint is used. The SSE stream the browser consumes is
produced by our own agent loop, whose events (tool calls, tool results, charts)
are richer than raw token deltas.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.exceptions import LLMError
from app.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    """Calls `POST /v1/messages` with the tool schemas built from the registry."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise LLMError(
                "Aucune clé API Anthropic n'est configurée.",
                detail="Renseignez DATAPILOT_ANTHROPIC_API_KEY ou repassez en DATAPILOT_LLM_PROVIDER=fake.",
            )
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """Send one turn and decode the answer into provider-neutral blocks."""
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": [_encode_message(message) for message in messages],
        }
        # TODO(R03): no retry/backoff yet — a single 429 or 529 fails the request.
        try:
            response = await self._client.post("/v1/messages", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(
                "Le service d'IA est injoignable.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise LLMError(
                "Le service d'IA a refusé la requête.",
                detail=_safe_error_detail(response),
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError("Réponse illisible du service d'IA.") from exc

        return LLMResponse(
            content=_decode_content(body.get("content", [])),
            stop_reason=body.get("stop_reason") or "end_turn",
        )

    async def aclose(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            await self._client.aclose()


def _encode_message(message: LLMMessage) -> dict[str, Any]:
    """Translate one neutral message into the Anthropic block format."""
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": blocks}


def _decode_content(raw_blocks: list[dict[str, Any]]) -> list[Any]:
    """Translate Anthropic blocks back into the neutral vocabulary."""
    decoded: list[Any] = []
    for block in raw_blocks:
        kind = block.get("type")
        if kind == "text":
            decoded.append(TextBlock(text=block.get("text", "")))
        elif kind == "tool_use":
            decoded.append(
                ToolUseBlock(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input") or {},
                )
            )
        # Any other block type (thinking, redacted content) is deliberately
        # ignored: the loop only acts on text and tool use.
    return decoded


def _safe_error_detail(response: httpx.Response) -> str:
    """Extract a short provider error message without leaking the request body."""
    try:
        body = response.json()
        message = body.get("error", {}).get("message")
    except ValueError:
        message = None
    return f"HTTP {response.status_code}: {message or 'erreur non détaillée'}"[:300]
