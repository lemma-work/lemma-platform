"""Extension point for plan-aware public app branding.

Cloud billing registers an implementation during startup. In OSS builds no
provider is registered, so the default policy denies removal and Lemma
attribution stays visible.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.modules.apps.domain.branding import AppBrandingEntitlementPort

AppBrandingEntitlementFactory = Callable[
    [object], Optional[AppBrandingEntitlementPort]
]

_factory: Optional[AppBrandingEntitlementFactory] = None


def configure_app_branding_entitlement_provider(
    factory: Optional[AppBrandingEntitlementFactory],
) -> None:
    """Register or clear the cloud branding-entitlement provider."""

    global _factory
    _factory = factory


def build_app_branding_entitlement_port(
    uow: object,
) -> Optional[AppBrandingEntitlementPort]:
    """Return the configured plan-aware provider, if this build has one."""

    return _factory(uow) if _factory is not None else None
