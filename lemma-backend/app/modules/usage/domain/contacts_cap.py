"""The contacts cap an organization is held to, and whether it is spent.

Three states, and the difference between the last two is the point: an
organization that never chose gets the deployment's default, so a bot anybody
can write to always has a ceiling; an owner who removed the cap chose no limit,
and the default must not quietly put one back.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.usage.domain.accounting import money


@dataclass(frozen=True, slots=True)
class StoredContactsCap:
    """What an owner set: a monthly limit, or ``None`` for no limit."""

    monthly_limit_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class ContactsCap:
    """The cap that applies this month."""

    limit_usd: Decimal | None
    #: Nobody in the organization has set one; this is the deployment's.
    is_default: bool


def effective_contacts_cap(
    stored: StoredContactsCap | None, *, default_usd: float | None
) -> ContactsCap:
    if stored is not None:
        return ContactsCap(limit_usd=stored.monthly_limit_usd, is_default=False)
    return ContactsCap(
        limit_usd=None if default_usd is None else money(default_usd),
        is_default=True,
    )


def cap_reached(cap: ContactsCap, *, consumed_usd: Decimal) -> bool:
    """Whether what was spent and is still reserved has reached the cap."""
    return cap.limit_usd is not None and consumed_usd >= cap.limit_usd
