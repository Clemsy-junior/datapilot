"""Conversation storage.

Deliberately naive: a dictionary guarded by a lock. It is honest about what it
is — the whole history disappears when the process restarts, and a second
replica would not see the first one's conversations.

# TODO(R01): replace with SQLite + SQLAlchemy so history survives a restart and
# more than one worker can serve the same conversation.
"""

from __future__ import annotations

import threading
import uuid

from app.exceptions import ConversationNotFoundError
from app.models.chat import Conversation


class ConversationStore:
    """In-memory conversation registry."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def create(self, dataset_id: str) -> Conversation:
        """Start a new conversation bound to a dataset."""
        conversation = Conversation(id=f"conv_{uuid.uuid4().hex[:12]}", dataset_id=dataset_id)
        with self._lock:
            self._conversations[conversation.id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        """Return a conversation or raise `ConversationNotFoundError`."""
        with self._lock:
            conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(
                f"La conversation « {conversation_id} » est introuvable.",
                detail="Elle a peut-être expiré au redémarrage du serveur.",
            )
        return conversation

    def get_or_create(self, conversation_id: str | None, dataset_id: str) -> Conversation:
        """Resume a conversation, or start one when no id is supplied."""
        if conversation_id is None:
            return self.create(dataset_id)
        return self.get(conversation_id)

    def count(self) -> int:
        """Number of stored conversations, used by tests and diagnostics."""
        with self._lock:
            return len(self._conversations)
