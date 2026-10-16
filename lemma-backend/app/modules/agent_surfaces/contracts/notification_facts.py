"""What the surfaces module announces when an asking conversation is owed no more answers.

Emitted in the transaction that records the *last* outstanding answer (or
expiry, or cancellation) for the notifications one agent run sent, so the
agent module's subscriber brings the conversation back with its item committed
alongside the answer -- an answer can no longer be recorded and then lost
before anyone acted on it.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace


class NotificationSettled(Fact):
    conversation_id: UUID
    pod_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["NotificationSettled"]
