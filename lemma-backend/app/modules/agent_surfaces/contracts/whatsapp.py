"""The pod-wide WhatsApp transport, for a module that is not a surface.

`identity` verifies a mobile number by sending a WhatsApp message, and the only
WhatsApp credentials and client in the platform are this module's. It was
`app/composition/identity_whatsapp.py`, which put `agent_surfaces.config` and
`agent_surfaces.platforms.whatsapp.client` into `identity`'s build for one send
and one settings read.

`GlobalWhatsAppConfiguration` is a snapshot rather than the settings object: it
names the six fields identity reads -- all six are read -- and keeps the three
secrets out of a repr. Publishing `surface_settings` itself would have made
every future WhatsApp setting part of this surface by default.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
platform client.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.config import reveal_secret
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
)
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)


logger = get_logger(__name__)


class GlobalWhatsAppDeliveryError(RuntimeError):
    """The shared global WhatsApp transport could not deliver a message."""


@dataclass(frozen=True, slots=True)
class GlobalWhatsAppConfiguration:
    """A redaction-safe snapshot of the existing global surface settings."""

    access_token: str | None = field(repr=False)
    phone_number_id: str | None
    display_phone_number: str | None
    app_secret: str | None = field(repr=False)
    verify_token: str | None = field(repr=False)
    webhook_security_enabled: bool


async def global_whatsapp_configuration() -> GlobalWhatsAppConfiguration:
    """The deployment's shared WhatsApp line: settings, or the `SHARED` pool row.

    **Settings are a pool of one.** Before there was a pool, the shared line was
    whatever `WHATSAPP_*` named, and that has to keep being true -- every
    deployment alive is configured that way and none of them should notice a
    pool arriving. But the reverse has to work too: a deployment that puts all
    of its numbers in the pool and sets no environment variables owns a shared
    line just as much, and refusing to see it would leave mobile verification
    quietly switched off with the credentials sitting in a table.

    So settings win where present and the `SHARED` row answers where they are
    not, field by field rather than whole-record -- a deployment that moved its
    token to the pool but left `WHATSAPP_PHONE_NUMBER_ID` in place gets the
    obvious answer instead of an arbitrary one.

    Async because the fallback is a read, and it opens its own session because
    the callers are identity's and identity has no SQL unit of work -- it is a
    Redis-backed service. Called once per verification start or status, so the
    session is not on a hot path.
    """
    if _settings_are_complete():
        return _from_settings()
    shared = await _shared_row()
    if shared is None:
        return _from_settings()
    return GlobalWhatsAppConfiguration(
        access_token=reveal_secret(surface_settings.whatsapp_access_token)
        or shared.access_token,
        phone_number_id=(
            surface_settings.whatsapp_phone_number_id or shared.phone_number_id
        ),
        display_phone_number=(
            surface_settings.whatsapp_display_phone_number
            or shared.display_phone_number
        ),
        app_secret=reveal_secret(surface_settings.whatsapp_app_secret)
        or shared.app_secret,
        verify_token=reveal_secret(surface_settings.whatsapp_verify_token)
        or shared.verify_token,
        webhook_security_enabled=surface_settings.surface_webhook_security_enabled,
    )


async def deployment_owns_whatsapp_number(phone_number_id: str) -> bool:
    """Is this a number we receive on -- the configured one, or one in the pool?

    One answer, because two disagreeing is a black hole. The ingress gate and
    identity's consumer both ask it, and when they disagreed a verification code
    sent to a pooled number was accepted by the first (so the webhook returned
    early and it never became an ordinary message) and rejected by the second
    (so it was never a verification either). It vanished.

    Every pooled number is a system number, so a person who sends their code to
    whichever of our numbers they are already talking to gets verified. Telling
    them "wrong number, use the other one" is a distinction only we can see.

    Fails **closed**, unlike `global_whatsapp_configuration`, and the asymmetry
    is deliberate: that one improves an answer settings can already give, while
    this one decides whether to act on a message. An unreadable pool must not
    turn into "sure, we own that".
    """
    if not phone_number_id:
        return False
    if phone_number_id == surface_settings.whatsapp_phone_number_id:
        return True
    try:
        async with async_session_maker() as session:
            found = await WhatsAppNumberRepository(
                SqlAlchemyUnitOfWork(session)
            ).get_by_phone_number_id(phone_number_id)
    except SQLAlchemyError:
        logger.warning(
            "agent_surfaces.whatsapp_contract.number_ownership_unreadable.degraded",
            exc_info=True,
        )
        return False
    return found is not None


async def _shared_row() -> WhatsAppNumberEntity | None:
    """The `SHARED` pool row, or nothing if it cannot be read.

    Fails soft on purpose. This is a *configuration* resolver on the path of
    every mobile verification, and the answer it exists to improve is one that
    settings can already give. A database that is unreachable, a migration not
    yet run, a caller with no database at all -- none of those should turn
    "verification works from settings" into an exception, which is what a bare
    read here would do to any deployment whose settings are merely incomplete.
    """
    try:
        async with async_session_maker() as session:
            return await WhatsAppNumberRepository(
                SqlAlchemyUnitOfWork(session)
            ).oldest_available_number()
    except SQLAlchemyError:
        # The database, specifically -- unreachable, or a migration not yet run.
        # Not `Exception`: a `TypeError` here is a bug in this resolver, and
        # swallowing it would turn a broken fallback into a silently
        # settings-only deployment that nobody notices until the pool is the
        # only place the credentials live.
        logger.warning(
            "agent_surfaces.whatsapp_contract.shared_number_unreadable.degraded",
            exc_info=True,
        )
        return None


def _from_settings() -> GlobalWhatsAppConfiguration:
    return GlobalWhatsAppConfiguration(
        access_token=reveal_secret(surface_settings.whatsapp_access_token),
        phone_number_id=surface_settings.whatsapp_phone_number_id,
        display_phone_number=surface_settings.whatsapp_display_phone_number,
        app_secret=reveal_secret(surface_settings.whatsapp_app_secret),
        verify_token=reveal_secret(surface_settings.whatsapp_verify_token),
        webhook_security_enabled=surface_settings.surface_webhook_security_enabled,
    )


def _settings_are_complete() -> bool:
    """Everything the shared line needs, already in the environment.

    Checked before the read rather than after, so the overwhelmingly common
    deployment -- one number, in settings, no pool at all -- pays nothing for a
    feature it is not using.
    """
    return bool(
        reveal_secret(surface_settings.whatsapp_access_token)
        and surface_settings.whatsapp_phone_number_id
        and reveal_secret(surface_settings.whatsapp_app_secret)
        and reveal_secret(surface_settings.whatsapp_verify_token)
    )


async def send_global_whatsapp_text(
    *,
    to: str,
    body: str,
    reply_to_message_id: str | None = None,
) -> bool:
    """Send identity feedback through the existing global WhatsApp transport."""
    whatsapp = await global_whatsapp_configuration()
    if not whatsapp.access_token or not whatsapp.phone_number_id:
        return False
    client = WhatsAppClient(
        access_token=whatsapp.access_token,
        phone_number_id=whatsapp.phone_number_id,
    )
    try:
        await client.send_text(
            phone_number_id=whatsapp.phone_number_id,
            to=to,
            body=body,
            reply_to_message_id=reply_to_message_id,
        )
    except (httpx.HTTPError, WhatsAppApiError) as exc:
        raise GlobalWhatsAppDeliveryError from exc
    return True
