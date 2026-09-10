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
from app.modules.pod.contracts.agent_access import pod_organization_id


@dataclass(frozen=True, slots=True)
class OnboardingTransport:
    event: ParsedInboundSurfaceEvent
    surface: AgentSurfaceEntity | None
    organization_id: UUID | None
    credentials: dict[str, JsonValue] = field(repr=False)
    binding_key: str


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
        receiver_ids=[request.surface_id]
        if isinstance(request, SurfaceDirectWebhookIngress)
        else request.receiver_surface_ids,
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
        return _shared_transport(request, surfaces, parsed)
    return await _installation_transport(surfaces, parsed, uow_factory)


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
            surfaces = await repository.list_active_by_type(platform.value)
            if request.receiver_surface_ids is not None:
                surfaces = [
                    surface
                    for surface in surfaces
                    if surface.id in request.receiver_surface_ids
                ]
                if not surfaces and request.receiver_surface_ids:
                    return None
    return platform, surfaces


def _shared_transport(
    request: SurfaceIngressRequest,
    surfaces: list[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
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
    )


async def _installation_transport(
    surfaces: list[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    uow_factory: UnitOfWorkFactory,
) -> OnboardingTransport:
    platform = parsed.platform
    if not parsed.tenant_id or not surfaces:
        raise PrivateDeliveryUnavailable(
            "The company installation could not be resolved"
        )
    async with uow_factory() as uow:
        ownership = [
            (surface, await pod_organization_id(uow, surface.pod_id))
            for surface in surfaces
        ]
        organization_ids = {organization_id for _, organization_id in ownership}
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
        organization_id = ownership[0][1]
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
    )
