"""Branding entitlement contract for hosted app entrypoints.

The apps module owns the question it needs answered: may the organization that
owns this pod remove Lemma attribution? Cloud billing can register a provider
that resolves the organization subscription. OSS has no billing provider, so
whether branding shows is decided by configuration alone. The feature is off by
default for now (``APP_BRANDING_ENABLED``); this port is only asked once an
operator turns it on.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class AppBrandingEntitlementPort(Protocol):
    async def can_remove_app_branding(self, *, pod_id: UUID) -> bool:
        """Return whether the owning organization may remove app branding."""

        ...
