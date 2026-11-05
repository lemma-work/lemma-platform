"""What account deletion asks of whoever bills this deployment.

Deleting an account must not leave a person paying for it. Billing lives
outside this repository, so identity states the one question it needs answered
-- may this person's account go now, and if not, what should they be told --
and a billing provider answers it by registering a guard at startup, the way
:mod:`app.modules.usage.services.usage_limit_provider` takes a limit port.

A deployment with no plans has nothing to bill, and deletion needs no guard
there. A deployment that has plans but registered no guard is refused instead
of waved through: that is a billing provider that has not yet been taught
about deletion, and guessing "nothing to pay for" is the guess that keeps
charging somebody whose account is gone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AccountDeletionRefusal:
    """Why the account cannot be deleted yet, in words fit to show the person.

    ``code`` is stable for clients to branch on; ``message`` should say what
    to do, such as cancelling a subscription first.
    """

    code: str
    message: str


class AccountDeletionGuard(Protocol):
    async def refusal(
        self, *, user_id: UUID, deleted_organization_ids: Sequence[UUID]
    ) -> AccountDeletionRefusal | None:
        """``None`` when nothing billed stands in the way.

        ``deleted_organization_ids`` are the organizations that go with the
        account because the person is their only member. Anything paid for
        there goes with them, so a guard should weigh those subscriptions as
        well as the person's own.
        """
        ...


#: Takes the request's unit of work, so a guard can read subscriptions in the
#: same transaction the deletion is decided in.
AccountDeletionGuardFactory = Callable[[object], AccountDeletionGuard]

_factory: AccountDeletionGuardFactory | None = None


def configure_account_deletion_guard(
    factory: AccountDeletionGuardFactory | None,
) -> None:
    """Register (or clear, with ``None``) the billing provider's guard."""
    global _factory
    _factory = factory


def build_account_deletion_guard(uow: object) -> AccountDeletionGuard | None:
    """The registered guard for this unit of work, or ``None`` when there is none."""
    return _factory(uow) if _factory is not None else None


__all__ = [
    "AccountDeletionGuard",
    "AccountDeletionGuardFactory",
    "AccountDeletionRefusal",
    "build_account_deletion_guard",
    "configure_account_deletion_guard",
]
