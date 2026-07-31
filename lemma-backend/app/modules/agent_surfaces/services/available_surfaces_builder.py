"""Builds the read-only "available surfaces" catalog for the frontend.

Joins the canonical surface->connector registry (``SURFACE_CONNECTOR_BINDINGS``)
with the connector catalog and the platform's system-credential availability, so
the frontend can render the setup UI and orchestrate account connection
generically — and adding a new surface (e.g. Discord) is a backend-only change,
picked up automatically by iterating the registry.

Kept out of ``AgentSurfaceService`` on purpose: that service doesn't hold a
connector service, and this is a pure catalog join with no surface state. The
controller injects the connector service and calls this builder directly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import (
    AvailableSurface,
    AvailableSurfacesResponse,
    SurfaceConnectDescriptor,
    SurfaceSystemClaim,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    has_native_credentials,
)
from app.modules.connectors.contracts import AuthProvider, ConnectorNotFoundError
from app.composition.surface_connectors import ConnectorService

logger = get_logger(__name__)


def _supported_credential_modes(
    platform: SurfacePlatform,
) -> list[SurfaceCredentialMode]:
    """CUSTOM (connect an account) is always possible; SYSTEM (a Lemma-managed bot
    that runs with no account) only when the platform's native credentials are
    actually configured in this environment."""
    modes = [SurfaceCredentialMode.CUSTOM]
    if has_native_credentials(platform):
        modes.append(SurfaceCredentialMode.SYSTEM)
    return modes


async def _connect_descriptor(
    connector_service: ConnectorService, connector_id: str
) -> tuple[SurfaceConnectDescriptor | None, bool, str | None, str | None, str | None]:
    """Resolve the connector's LEMMA capability into a connect descriptor plus its
    catalog display fields. Returns ``(descriptor, available, title, description,
    icon)``; ``available`` is False (and descriptor None) when the connector is
    missing, inactive, or exposes no LEMMA capability — so a mis-configured or
    not-yet-catalogued surface degrades to a visible "unavailable" row instead of
    500-ing the whole endpoint."""
    try:
        connector = await connector_service.get_connector(connector_id)
    except ConnectorNotFoundError:
        return None, False, None, None, None
    if not connector.is_active:
        return None, False, connector.title, connector.description, connector.icon
    try:
        capability = connector.capability_for(AuthProvider.LEMMA)
    except ValueError:
        logger.debug(
            'agent_surfaces.available_surfaces_builder.surface_connector_s_has_no.diagnostic',
            connector_id=connector_id,
        )
        return None, False, connector.title, connector.description, connector.icon

    descriptor = SurfaceConnectDescriptor(
        auth_scheme=capability.auth_scheme,
        auth_config_schema=capability.auth_config_schema,
        credential_schema=capability.credential_schema,
        system_oauth_available=bool(
            getattr(capability, "system_default_available", False)
        ),
        supports_org_custom_oauth=bool(
            getattr(capability, "supports_org_custom_oauth", False)
        ),
    )
    return descriptor, True, connector.title, connector.description, connector.icon


async def _system_claim(
    platform: SurfacePlatform,
    *,
    modes: list[SurfaceCredentialMode],
    pod_id: UUID | None,
    surface_repository: SurfaceInstallationRepositoryPort | None,
) -> SurfaceSystemClaim | None:
    """Who, if anyone, already holds this platform's Lemma-managed identity.

    The shared bot/number is claimable once per organization (enforced on write
    by ``_ensure_unique_org_credential_binding``); returning it here lets the
    setup UI disable the option up front rather than surfacing a failed save.
    Only meaningful when the platform actually has a SYSTEM mode; best-effort,
    because a catalog read must not fail on a repository hiccup."""
    if SurfaceCredentialMode.SYSTEM not in modes:
        return None
    if pod_id is None or surface_repository is None:
        return SurfaceSystemClaim(available=True)
    try:
        conflict = await surface_repository.get_system_credential_conflict_in_org(
            pod_id=pod_id, platform=platform.value
        )
    except SQLAlchemyError:
        logger.debug(
            "agent_surfaces.available_surfaces_builder.system_claim_lookup_failed.diagnostic",
            platform=platform.value,
        )
        return SurfaceSystemClaim(available=True)
    if not isinstance(conflict, AgentSurfaceEntity):
        return SurfaceSystemClaim(available=True)
    return SurfaceSystemClaim(
        available=False,
        claimed_by_pod_id=conflict.pod_id,
        claimed_by_surface_name=conflict.name,
    )


def _managed_setup_available(platform: SurfacePlatform) -> bool:
    """Whether a dedicated bot can be provisioned for the user here.

    Telegram can hand someone their own bot through a manager bot, but only when
    this deployment has one configured. Publishing that as catalog data keeps the
    setup UI from offering a path that dead-ends after the user commits to it.
    """
    if platform is not SurfacePlatform.TELEGRAM:
        return False
    return bool(
        surface_settings.telegram_manager_bot_token
        and surface_settings.telegram_manager_bot_username
    )


async def build_available_surfaces(
    *,
    connector_service: ConnectorService,
    pod_id: UUID | None = None,
    surface_repository: SurfaceInstallationRepositoryPort | None = None,
) -> AvailableSurfacesResponse:
    """The connectable-surface catalog: one row per registry platform.

    ``pod_id``/``surface_repository`` are optional so the catalog stays usable as
    a pure registry join; supply both to also resolve each platform's
    system-identity claim for the pod's organization."""
    surfaces: list[AvailableSurface] = []
    for platform, binding in SURFACE_CONNECTOR_BINDINGS.items():
        connect, available, title, description, icon = await _connect_descriptor(
            connector_service, binding.connector_id
        )
        modes = _supported_credential_modes(platform)
        surfaces.append(
            AvailableSurface(
                platform=platform,
                connector_id=binding.connector_id,
                provider=binding.provider,
                title=title,
                description=description,
                icon=icon,
                supported_credential_modes=modes,
                connector_available=available,
                connect=connect,
                system_claim=await _system_claim(
                    platform,
                    modes=modes,
                    pod_id=pod_id,
                    surface_repository=surface_repository,
                ),
                managed_setup_available=_managed_setup_available(platform),
            )
        )
    return AvailableSurfacesResponse(surfaces=surfaces)
