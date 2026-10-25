"""The contacts cap an organization is held to.

Three states, and the difference between the last two is the point: an
organization that never chose gets the deployment's default, so a bot anybody
can write to always has a ceiling; an owner who removed the cap chose no limit,
and the default must not quietly put one back. Each half of the cap -- what
answering may cost, and how many may be answered -- is chosen on its own, so an
owner who set only a spend still gets the default count, and the other way
round.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.usage.domain.accounting import money


@dataclass(frozen=True, slots=True)
class StoredContactsCap:
    """What an owner set: a monthly limit and a number of contacts, ``None``
    for no limit on either -- each only where its ``_set`` says they chose it."""

    monthly_limit_usd: Decimal | None
    max_contacts: int | None = None
    monthly_limit_set: bool = True
    max_contacts_set: bool = False


@dataclass(frozen=True, slots=True)
class ContactsCap:
    """The cap that applies this month."""

    limit_usd: Decimal | None
    #: Nobody in the organization has set one; this is the deployment's.
    is_default: bool
    #: How many contacts its bots may answer at all; ``None`` for no limit.
    max_contacts: int | None = None


def effective_contacts_cap(
    stored: StoredContactsCap | None,
    *,
    default_usd: float | None,
    default_max_contacts: int | None = None,
) -> ContactsCap:
    default_limit = None if default_usd is None else money(default_usd)
    if stored is None:
        return ContactsCap(
            limit_usd=default_limit,
            is_default=True,
            max_contacts=default_max_contacts,
        )
    return ContactsCap(
        limit_usd=(
            stored.monthly_limit_usd if stored.monthly_limit_set else default_limit
        ),
        is_default=not stored.monthly_limit_set,
        max_contacts=(
            stored.max_contacts if stored.max_contacts_set else default_max_contacts
        ),
    )


def may_answer_another_contact(cap: ContactsCap, *, answered: int) -> bool:
    """Whether a contact nobody has answered yet may be: the count has room."""
    return cap.max_contacts is None or answered < cap.max_contacts
