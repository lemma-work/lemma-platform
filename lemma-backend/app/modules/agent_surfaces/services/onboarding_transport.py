"""Authenticated transport selection happens before any personal pod routing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
    SurfaceCredentialMode,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import native_credentials
from app.modules.agent_surfaces.services.onboarding_submissions import (
    parse_native_submission,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
)
from app.modules.connectors.contracts.surfaces import account_with_secrets
from app.modules.pod.contracts.agent_access import pod_organization_ids

#: Where a platform-wide webhook arrives on Lemma's own shared bot, so a surface
#: bound to a customer's own account cannot be what it is for. Mirrors
#: `surface_inbound._has_shared_system_bot`, which owns the rule; duplicated
#: rather than imported because that module imports this one.
_SHARED_SYSTEM_BOT_PLATFORMS = frozenset(
    {SurfacePlatform.TELEGRAM, SurfacePlatform.WHATSAPP}
)


def _has_shared_system_bot(platform: SurfacePlatform) -> bool:
    return platform in _SHARED_SYSTEM_BOT_PLATFORMS


@dataclass(frozen=True, slots=True)
class OnboardingTransport:
    event: ParsedInboundSurfaceEvent
    surface: AgentSurfaceEntity | None
    organization_id: UUID | None
    credentials: dict[str, JsonValue] = field(repr=False)
    binding_key: str
    #: Which surfaces the bot that delivered this event serves, as the request
    #: gave them -- a native receiver (Telegram polling, the Slack socket) names
    #: its own, a direct webhook is the one it arrived on, and a platform-wide
    #: webhook names none. Carried because it is authorization, not a detail of
    #: delivery: two installations can share one Slack workspace, so the tenant
    #: alone does not say which of them may serve this message. Anything asking
    #: "could this person be served here" has to ask it of the same set
    #: ingestion will use, or it answers for a bot that is not listening.
    receiver_surface_ids: list[UUID] | None = None


def platform_binding_key(
    event: ParsedInboundSurfaceEvent, installation_id: UUID | None
) -> str:
    actor = event.sender_external_user_id
    if not actor:
        raise PrivateDeliveryUnavailable("The platform did not identify the sender")
    return hashlib.sha256(
        json.dumps(
            [
                event.platform.value,
                event.tenant_id or "",
                str(installation_id or "shared"),
                actor,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def resolve_onboarding_transport(
    request: SurfaceIngressRequest,
    *,
    uow_factory: UnitOfWorkFactory,
    adapters: SurfacePlatformAdapterRegistry,
) -> OnboardingTransport | None:
    receiver_ids = (
        [request.surface_id]
        if isinstance(request, SurfaceDirectWebhookIngress)
        else request.receiver_surface_ids
    )
    loaded = await _transport_candidates(request, uow_factory)
    if loaded is None:
        return None
    platform, surfaces = loaded
    if platform.is_email:
        return None
    adapter = adapters.get(platform)
    if adapter is None:
        return None
    parsed = await parse_native_submission(
        request.payload,
        platform=platform,
        uows=uow_factory,
        adapters=adapters,
        receiver_ids=receiver_ids,
    )
    if parsed is None:
        parsed = await adapter.parse_inbound_event(request.payload, request.headers)
    if parsed is None or (not parsed.is_dm and not parsed.mentioned_agent):
        return None
    surfaces = [
        surface
        for surface in surfaces
        if surface.is_active
        and surface.status.accepts_inbound_events()
        and surface.matches_tenant(parsed.tenant_id)
    ]
    if platform in (SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM):
        return _shared_transport(request, surfaces, parsed, receiver_ids)
    return await _installation_transport(surfaces, parsed, uow_factory, receiver_ids)


async def _transport_candidates(
    request: SurfaceIngressRequest, uow_factory: UnitOfWorkFactory
) -> tuple[SurfacePlatform, list[AgentSurfaceEntity]] | None:
    async with uow_factory() as uow:
        repository = SurfaceRepository(uow)
        if isinstance(request, SurfaceDirectWebhookIngress):
            direct = await repository.get(request.surface_id)
            if direct is None:
                return None
            platform = direct.surface_type
            if platform in (SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM) and (
                direct.account_id is not None
                or direct.credential_mode != SurfaceCredentialMode.SYSTEM
            ):
                return None
            surfaces = [direct]
        else:
            platform = SurfacePlatform.from_source(request.source)
            if platform is None or platform.is_email:
                return None
            # The same two narrowings the ordinary ingress path applies, asked
            # of the database rather than of the result. `list_active_by_type`
            # is the deployment's entire surface list for a platform and has no
            # production caller by design; reaching for it here would put that
            # read back on the path every inbound message takes.
            receiver_surface_ids = request.receiver_surface_ids
            if receiver_surface_ids is None and _has_shared_system_bot(platform):
                # The shared bot's transport is its system credentials, and
                # `_shared_transport` consults this list only to refuse a
                # *receiver-scoped* delivery -- which this is not. Loading it
                # read every system surface of the platform in the deployment on
                # the way to not using one, on every message a shared-bot sender
                # ever sends.
                return platform, []
            surfaces = await repository.list_active_for_routing(
                platform.value,
                surface_ids=receiver_surface_ids,
                system_credentials_only=(
                    receiver_surface_ids is None and _has_shared_system_bot(platform)
                ),
            )
            if receiver_surface_ids is not None and not surfaces:
                return None
    return platform, surfaces


def _shared_transport(
    request: SurfaceIngressRequest,
    surfaces: list[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    receiver_ids: list[UUID] | None,
) -> OnboardingTransport | None:
    platform = parsed.platform
    if not parsed.is_dm:
        return None
    # A receiver scoped to a customer bot may not invoke shared signup.
    if isinstance(request, SurfaceDirectWebhookIngress) and any(
        surface.account_id is not None for surface in surfaces
    ):
        return None
    if (
        not isinstance(request, SurfaceDirectWebhookIngress)
        and request.receiver_surface_ids is not None
        and any(
            surface.account_id is not None
            or surface.credential_mode != SurfaceCredentialMode.SYSTEM
            for surface in surfaces
        )
    ):
        return None
    credentials = TypeAdapter(dict[str, JsonValue]).validate_python(
        native_credentials(platform)
    )
    # Shared signup belongs to the shared line, and only to it. This reads like
    # a leftover single-number assumption now that numbers come from a pool, and
    # it is the opposite: a pooled number is held by one organisation, so an
    # unknown sender who reaches it is that organisation's surface's business,
    # not the deployment's signup flow. Answering them here would introduce them
    # to Lemma-at-large from a number somebody bought for their own customers.
    #
    # It compares against settings rather than the pool because this function is
    # synchronous and has no unit of work, and because the number it is asking
    # about is the one the deployment configured -- which is what `SHARED` in
    # `surface_whatsapp_numbers` mirrors rather than replaces.
    if platform == SurfacePlatform.WHATSAPP and (
        not credentials.get("phone_number_id")
        or parsed.reply_target.get("phone_number_id")
        != credentials.get("phone_number_id")
    ):
        return None
    return OnboardingTransport(
        parsed,
        None,
        None,
        credentials,
        platform_binding_key(parsed, None),
        receiver_ids,
    )


async def _installation_transport(
    surfaces: list[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    uow_factory: UnitOfWorkFactory,
    receiver_ids: list[UUID] | None,
) -> OnboardingTransport:
    platform = parsed.platform
    if not parsed.tenant_id or not surfaces:
        raise PrivateDeliveryUnavailable(
            "The company installation could not be resolved"
        )
    async with uow_factory() as uow:
        # One statement, not one per surface. This asked `pod_organization_id`
        # inside the comprehension purely to build the set below, so a workspace
        # with twenty surfaces paid twenty round trips to answer "do they all
        # belong to one organization" -- on the path a person's first private
        # message takes.
        by_pod = await pod_organization_ids(
            uow, {surface.pod_id for surface in surfaces}
        )
        organization_ids = {by_pod.get(surface.pod_id) for surface in surfaces}
        account_ids = {surface.account_id for surface in surfaces}
        if (
            len(organization_ids) != 1
            or None in organization_ids
            or len(account_ids) != 1
        ):
            raise PrivateDeliveryUnavailable(
                "The company installation ownership is ambiguous"
            )
        installation = min(surfaces, key=lambda surface: str(surface.id))
        organization_id = by_pod[installation.pod_id]
        credentials = TypeAdapter(dict[str, JsonValue]).validate_python(
            native_credentials(platform, surface=installation)
        )
        if installation.account_id is not None:
            found = await account_with_secrets(uow, installation.account_id)
            if (
                found is None
                or found[0].organization_id != organization_id
                or found[0].status != "CONNECTED"
            ):
                raise PrivateDeliveryUnavailable(
                    "The installation credentials belong to another organization"
                )
            credentials = TypeAdapter(dict[str, JsonValue]).validate_python(found[1])
    return OnboardingTransport(
        parsed,
        installation,
        organization_id,
        credentials,
        platform_binding_key(parsed, installation.account_id or installation.id),
        receiver_ids,
    )
