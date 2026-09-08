"""Request-scoped dependencies.

Everything long-lived (dataset store, conversation store, LLM provider) is built
once in the lifespan handler and stored on `app.state`. Endpoints reach it
through these functions, so a test can swap any of them by overriding a single
dependency instead of monkey-patching module globals.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.agent.loop import AgentRunner
from app.config import Settings, get_settings
from app.conversations import ConversationStore
from app.datasets.store import DatasetStore
from app.llm.base import LLMProvider


def get_dataset_store(request: Request) -> DatasetStore:
    """Return the process-wide dataset store."""
    return request.app.state.dataset_store


def get_conversation_store(request: Request) -> ConversationStore:
    """Return the process-wide conversation store."""
    return request.app.state.conversation_store


def get_llm_provider(request: Request) -> LLMProvider:
    """Return the configured LLM provider."""
    return request.app.state.llm_provider


def get_app_settings(request: Request) -> Settings:
    """Return the settings this application was built with."""
    return getattr(request.app.state, "settings", None) or get_settings()


def get_agent_runner(
    store: Annotated[DatasetStore, Depends(get_dataset_store)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AgentRunner:
    """Build the agent runner for one request."""
    return AgentRunner(provider=provider, store=store, settings=settings)


DatasetStoreDep = Annotated[DatasetStore, Depends(get_dataset_store)]
ConversationStoreDep = Annotated[ConversationStore, Depends(get_conversation_store)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
AgentRunnerDep = Annotated[AgentRunner, Depends(get_agent_runner)]
