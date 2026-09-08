"""Application exceptions.

They all carry a machine-readable `code` and a message written for a human,
because both ends need them: the front end branches on the code, the user reads
the message, and — for tool failures — the *LLM* reads the message and is
expected to recover from it.
"""

from __future__ import annotations


class DataPilotError(Exception):
    """Base class for every error the application raises on purpose."""

    code = "internal_error"
    status_code = 500

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class DatasetNotFoundError(DataPilotError):
    """The requested dataset id is unknown to the store."""

    code = "dataset_not_found"
    status_code = 404


class ConversationNotFoundError(DataPilotError):
    """The requested conversation id is unknown."""

    code = "conversation_not_found"
    status_code = 404


class InvalidDatasetError(DataPilotError):
    """An uploaded file could not be read as a usable CSV."""

    code = "invalid_dataset"
    status_code = 400


class ToolError(DataPilotError):
    """A tool refused its arguments or could not answer.

    This is *not* a bug: it is a normal branch of the agent loop. The message is
    fed back to the LLM as the tool result so it can pick another column, another
    aggregation, or explain the limitation to the user.
    """

    code = "tool_error"
    status_code = 400


class LLMError(DataPilotError):
    """The LLM provider failed or answered something unusable."""

    code = "llm_error"
    status_code = 502
