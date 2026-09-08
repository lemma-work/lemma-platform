"""Extension point for supplying a :class:`UsageLimitPort`.

Dependency inversion: a billing/plan provider *implements* usage's port and
registers a factory here at startup (see the billing module's lifespan hooks).
When nothing is registered, a deployment that has stated limits in settings
gets the configuration-backed port
(:mod:`app.modules.usage.infrastructure.configured_usage_limit_port`) — so
spend limits are configurable without shipping a billing module. When neither
is present, usage remains unlimited while still recording metering data.

This is a single, typed extension point for one port, NOT a generic capability
registry: usage owns the contract; the provider plugs in.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.modules.usage.config import usage_settings
from app.modules.usage.domain.ports import UsageLimitPort

# A factory takes a unit of work (so the adapter can read plans/subscriptions
# transactionally) and returns a port, or None for unlimited OSS metering.
UsageLimitPortFactory = Callable[[object], Optional[UsageLimitPort]]

_factory: Optional[UsageLimitPortFactory] = None


def configure_usage_limit_provider(factory: Optional[UsageLimitPortFactory]) -> None:
    """Register (or clear) the limit-port factory. Idempotent; last write wins."""
    global _factory
    _factory = factory


def build_usage_limit_port(uow: object) -> Optional[UsageLimitPort]:
    """Resolve the limit port for this unit of work, or None when unconfigured."""
    if _factory is not None:
        return _factory(uow)
    from app.modules.usage.infrastructure.configured_usage_limit_port import (
        configured_usage_limit_port,
    )

    return configured_usage_limit_port(uow)


def usage_limits_are_possible() -> bool:
    """Whether this deployment can apply a monetary limit to anything.

    Startup-knowable, unlike whether a limit applies to a *particular* org: a
    plan-backed provider answers that per subscription. Either a provider is
    registered, or settings state a limit. Neither, and unpriced models cost
    nothing -- metering still records tokens, and there is no budget to enforce.
    """
    if _factory is not None:
        return True
    return any(
        limit is not None
        for limit in (
            usage_settings.usage_org_monthly_limit_usd,
            usage_settings.usage_user_weekly_limit_usd,
            usage_settings.usage_user_monthly_limit_usd,
        )
    )
