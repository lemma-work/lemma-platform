"""Hooks, for the webhook endpoint that takes their deliveries.

``connectors`` holds the plugin that answers ``POST /webhooks/hook``, beside
the other providers' plugins; this is everything it may ask of a hook: whether
an address proved itself and what it sent (``admit_hook_delivery``), and the
row that becomes (``keep_hook_delivery``). See ``domain/hooks`` for the rules.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.datastore.domain.hooks import (
    HOOK_PARAM,
    HOOK_SOURCE,
    KEY_PARAM,
    HookDeliveryRefused,
)
from app.modules.datastore.services.hooks import (
    HookReceipt,
    admit_delivery,
    keep_delivery,
)


def _default_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def admit_hook_delivery(
    *,
    hook_id: UUID,
    key: str,
    content_type: str | None,
    raw_body: bytes,
    uow_factory: UnitOfWorkFactory | None = None,
) -> dict[str, object] | None:
    """The body, once the address proved itself; ``None`` when it did not.

    Raises :class:`HookDeliveryRefused` for a delivery that proved itself and
    still cannot be taken.
    """
    return await admit_delivery(
        uow_factory or _default_factory(),
        hook_id=hook_id,
        key=key,
        content_type=content_type,
        raw_body=raw_body,
    )


async def keep_hook_delivery(
    *,
    hook_id: UUID,
    body: dict[str, object],
    uow_factory: UnitOfWorkFactory | None = None,
) -> HookReceipt:
    """Add the row the delivery makes. Raises :class:`HookDeliveryRefused`."""
    return await keep_delivery(
        uow_factory or _default_factory(), hook_id=hook_id, body=body
    )


__all__ = [
    "HOOK_PARAM",
    "HOOK_SOURCE",
    "KEY_PARAM",
    "HookDeliveryRefused",
    "HookReceipt",
    "admit_hook_delivery",
    "keep_hook_delivery",
]
