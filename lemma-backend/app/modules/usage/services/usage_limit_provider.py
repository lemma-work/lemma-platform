"""Extension point for supplying a :class:`UsageLimitPort`.

Dependency inversion: a billing/plan provider *implements* usage's port and
registers a factory here at startup (see the billing module's lifespan hooks).
When nothing is registered — e.g. an open-source build with no billing module —
usage remains unlimited while still recording metering data.

This is a single, typed extension point for one port, NOT a generic capability
registry: usage owns the contract; the provider plugs in.
"""

from __future__ import annotations

from typing import Callable, Optional

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
    return _factory(uow) if _factory is not None else None
