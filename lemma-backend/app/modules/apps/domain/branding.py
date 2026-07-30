"""Branding entitlement contract for hosted app entrypoints.

The apps module owns the question it needs answered: may the organization that
owns this pod remove Lemma attribution? Cloud billing can register a provider
that resolves the organization subscription. OSS has no billing provider, so
branding remains enabled by default unless the self-host operator disables the
feature through configuration.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class AppBrandingEntitlementPort(Protocol):
    async def can_remove_app_branding(self, *, pod_id: UUID) -> bool:
        """Return whether the owning organization may remove app branding."""

        ...
