"""Telling somebody their message was lost, once, when it really was.

A message that fails in our own pipeline -- the onboarding step raised, the job
queue was down, the worker crashed mid-preparation -- used to end in a
dead-lettered inbox row and nothing else. From the sender's side it looked like
being ignored: the message showed as delivered and nobody ever answered it.

So the final attempt says so. Only the final one, because an earlier failure is
retried and usually goes through, and an apology followed by the answer reads
as the product not knowing what it is doing. And only once per message, keyed
on the platform's own message id, because a webhook can be redelivered after
its last attempt was counted.

Everything here runs on a failure path and must not raise into it: the caller
gathers it with ``return_exceptions=True`` and logs what comes back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.request_context import current_observability_context
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    arrival_number,
)

logger = get_logger(__name__)

#: Builds the credential resolver a notice answers with, from a unit of work.
ResolverFactory = Callable[..., SurfaceCredentialResolver]


FAILURE_NOTICE = (
    "Sorry, something went wrong on our side and I couldn't process your "
    "message. Please send it again."
)

#: Appended to the platform in the dedup key, so the notice claims its own key
#: space: the message's ordinary claim was spent (and possibly handed back) by
#: the path that failed, and the two must not answer for each other.
_NOTICE_KEY_SUFFIX = ":failure-notice"


async def tell_sender_it_failed(
    request: SurfaceIngressRequest,
    *,
    source: str,
    uow_factory: UnitOfWorkFactory,
    event_dedup_store: SurfaceEventDedupStorePort,
    adapters: SurfacePlatformAdapterRegistry | None = None,
    resolvers: ResolverFactory | None = None,
) -> bool:
    """Send the sender of ``request`` one line saying it was not processed.

    ``True`` when the line went out. ``False`` when there is nobody to tell -- a
    body that is not a message, a platform with no adapter -- or when this
    message was already told.
    """
    return await tell_sender(
        request,
        text=FAILURE_NOTICE,
        key=_NOTICE_KEY_SUFFIX,
        source=source,
        uow_factory=uow_factory,
        event_dedup_store=event_dedup_store,
        adapters=adapters,
        resolvers=resolvers,
    )


async def tell_sender(
    request: SurfaceIngressRequest,
    *,
    text: str,
    key: str,
    source: str,
    uow_factory: UnitOfWorkFactory,
    event_dedup_store: SurfaceEventDedupStorePort,
    adapters: SurfacePlatformAdapterRegistry | None = None,
    resolvers: ResolverFactory | None = None,
) -> bool:
    """Say ``text`` to whoever sent ``request``, once per message per ``key``.

    For the replies that are about the pipeline rather than the conversation:
    nothing was routed, so there is no conversation to answer in -- only the
    message, the platform it came from, and the number it arrived on.
    """
    adapters = adapters or SurfacePlatformAdapterRegistry()
    resolved = await _sender_of(request, source=source, adapters=adapters)
    if resolved is None:
        return False
    adapter, parsed, platform = resolved
    claimed = await event_dedup_store.claim_message(
        # Not the surface: the worker that may tell the same message knows
        # only the platform's ids, and the two must claim one key.
        surface_installation_id=None,
        platform=f"{platform}{key}",
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
        external_message_id=parsed.external_message_id,
    )
    if not claimed:
        return False
    credentials = await _credentials(
        uow_factory, request, platform=platform, parsed=parsed, resolvers=resolvers
    )
    await adapter.send_message(credentials=credentials, event=parsed, message=text)
    logger.info(
        "agent_surfaces.processing_failure_notice.sender_told.observed",
        platform=platform,
        notice=key.lstrip(":"),
    )
    return True


async def tell_chat_sender_it_failed(
    context: SurfaceChatContext,
    *,
    uow_factory: UnitOfWorkFactory,
    event_dedup_store: SurfaceEventDedupStorePort,
    adapters: SurfacePlatformAdapterRegistry | None = None,
) -> bool:
    """The same notice, for a message that was queued and then failed to run.

    The worker holds the parsed context rather than the webhook, so there is
    nothing to parse -- and the claim is keyed the same way, so a message that
    failed in both places is still told once.
    """
    adapter = (adapters or SurfacePlatformAdapterRegistry()).get(context.platform)
    parsed = context.event
    if adapter is None or not parsed.external_message_id:
        return False
    platform = context.platform.value
    claimed = await event_dedup_store.claim_message(
        surface_installation_id=None,
        platform=f"{platform}{_NOTICE_KEY_SUFFIX}",
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
        external_message_id=parsed.external_message_id,
    )
    if not claimed:
        return False
    async with uow_factory() as uow:
        credentials = await SurfaceCredentialResolver(uow=uow).for_platform(
            context.platform,
            context.surface_account_id,
            surface=None,
            arrived_on=arrival_number(parsed),
        )
    await adapter.send_message(
        credentials=credentials, event=parsed, message=FAILURE_NOTICE
    )
    logger.info(
        "agent_surfaces.processing_failure_notice.sender_told.observed",
        platform=platform,
        notice=_NOTICE_KEY_SUFFIX.lstrip(":"),
    )
    return True


async def tell_failed_senders(
    requests: list[SurfaceIngressRequest],
    *,
    source: str,
    uow_factory: UnitOfWorkFactory,
    event_dedup_store: SurfaceEventDedupStorePort,
) -> None:
    """One line to each sender whose message is not going to be answered.

    On a path that is already failing, so nothing here may replace the error
    being propagated: each notice is gathered, and one that fails is logged.
    """
    told = await asyncio.gather(
        *(
            tell_sender_it_failed(
                request,
                source=source,
                uow_factory=uow_factory,
                event_dedup_store=event_dedup_store,
            )
            for request in requests
        ),
        return_exceptions=True,
    )
    for outcome in told:
        if isinstance(outcome, Exception):
            logger.warning(
                "agent_surfaces.processing_failure_notice.notice_not_sent.degraded",
                source=source,
                exc_info=outcome,
            )


def final_job_attempt(max_attempts: int) -> bool:
    """Is the running worker job on its last try?"""
    attempt = current_observability_context().job_attempt
    return attempt is not None and attempt >= max_attempts


async def tell_chat_sender_safely(
    context: AgentSurfaceContext, *, uow_factory: UnitOfWorkFactory
) -> None:
    """`tell_chat_sender_it_failed` for a failure path: never raises into it."""
    if not isinstance(context, SurfaceChatContext):
        return
    (outcome,) = await asyncio.gather(
        tell_chat_sender_it_failed(
            context,
            uow_factory=uow_factory,
            event_dedup_store=get_surface_event_dedup_store(),
        ),
        return_exceptions=True,
    )
    if isinstance(outcome, Exception):
        logger.warning(
            "agent_surfaces.processing_failure_notice.notice_not_sent.degraded",
            source=context.platform.value,
            exc_info=outcome,
        )


async def _sender_of(
    request: SurfaceIngressRequest,
    *,
    source: str,
    adapters: SurfacePlatformAdapterRegistry,
) -> tuple[SurfacePlatformAdapterPort, ParsedInboundSurfaceEvent, str] | None:
    platform = platform_value_for_source(source)
    adapter = adapters.get(platform) if platform else None
    if adapter is None or platform is None:
        return None
    parsed = await adapter.parse_inbound_event(request.payload, request.headers)
    if parsed is None or not parsed.external_message_id:
        # Without a message id there is no way to say this once, and a notice
        # that may repeat on every redelivery is worse than none.
        return None
    return adapter, parsed, platform


async def _credentials(
    uow_factory: UnitOfWorkFactory,
    request: SurfaceIngressRequest,
    *,
    platform: str,
    parsed: ParsedInboundSurfaceEvent,
    resolvers: ResolverFactory | None,
) -> dict[str, object]:
    """What to answer with: the number the message arrived on, on a pooled line.

    Read in a short scope of its own, closed before the send.
    """
    async with uow_factory() as uow:
        resolver = (resolvers or SurfaceCredentialResolver)(uow=uow)
        if isinstance(request, SurfaceDirectWebhookIngress):
            surface = await SurfaceRepository(uow).get(request.surface_id)
            if surface is not None:
                return await resolver.for_surface(
                    surface, arrived_on=arrival_number(parsed)
                )
        return await resolver.for_platform(
            platform, None, surface=None, arrived_on=arrival_number(parsed)
        )


__all__ = [
    "FAILURE_NOTICE",
    "final_job_attempt",
    "tell_chat_sender_it_failed",
    "tell_chat_sender_safely",
    "tell_failed_senders",
    "tell_sender",
    "tell_sender_it_failed",
]
