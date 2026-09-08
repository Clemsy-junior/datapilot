"""Structured error envelope.

Every failure leaving the API has the same shape, so the front end has exactly
one error branch to write. Stack traces stay in the server logs: `detail` is a
short, user-facing clarification, never an exception repr.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """The error itself."""

    code: str
    message: str
    detail: str | None = None


class ErrorEnvelope(BaseModel):
    """Wrapper so a client can tell an error payload from a success payload."""

    error: ErrorBody
