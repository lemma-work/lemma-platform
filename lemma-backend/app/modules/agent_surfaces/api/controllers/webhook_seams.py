"""The collaborators the pooled-number webhook routes take, not reach for.

A route that builds its own repository and calls a publisher through a class
method can only be tested by replacing names inside the controller's module --
which is a test certifying the half it wrote, and which keeps passing after the
route stops calling them. These are the two edges those routes have: one read,
one accept. `Depends` supplies the real ones in the app and a test passes its
own, so `scripts/check_test_doubles.py` has nothing to count.

Their own module rather than the controller's, because the controller is at the
line ceiling and these are the part of it that is not a route.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.pooled_numbers import pooled_number

from collections.abc import Awaitable, Callable

from fastapi import Depends

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.authorization.scope import uow_scope
from app.modules.agent_surfaces.services.inbound_acceptor import (
    AcceptResult,
    InboundAcceptor,
    InboundDelivery,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity

#: Reads one pool row by the phone number id a callback path names.
#:
#: A named collaborator rather than a call the route makes for itself, because
#: the two pooled-number routes are worth testing without a database and the
#: alternative is reaching into this module to replace the repository class --
#: which is a test certifying the half it wrote. `Depends` supplies the real
#: one in the app; a test passes its own.
PooledNumberLookup = Callable[[str], Awaitable["WhatsAppNumberEntity | None"]]

#: Classifies one inbound delivery and answers or stores it, in the request.
#:
#: Same reason as `PooledNumberLookup`: a route's only outbound edge should be
#: something the caller can supply, not a class reached through this module's
#: globals.
InboundAccept = Callable[[InboundDelivery], Awaitable[AcceptResult]]


def get_pooled_number_lookup(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> PooledNumberLookup:
    """The real pool lookup: one short session scope per read.

    ``None`` from it is ordinary rather than an error -- a deployment with a
    single WhatsApp number has no pool rows at all, and every credential on
    these routes falls back to ``surface_settings.whatsapp_*`` when the row, or
    the column on it, is absent.
    """

    async def lookup(phone_number_id: str) -> WhatsAppNumberEntity | None:
        async with uow_scope(uow_factory) as uow:
            return await pooled_number(uow, phone_number_id)

    return lookup


def get_inbound_accept(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> InboundAccept:
    """The real acceptor, over the request's unit-of-work factory."""
    return InboundAcceptor(uow_factory).accept
