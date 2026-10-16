"""What the identity module announces when a person's profile changes.

``UserMobileChanged`` is emitted in the transaction that changes (or clears) a
user's verified mobile number, so whatever was bound to the old number -- a
surface identity resolved through it -- is unbound by work committed with the
change.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import SYSTEM_NAMESPACE, Fact


class UserMobileChanged(Fact):
    user_id: UUID

    def work_namespace(self) -> str:
        # A person is not scoped to one pod or organization.
        return SYSTEM_NAMESPACE


__all__ = ["UserMobileChanged"]
