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

from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import (
    AvailableSurface,
    AvailableSurfacesResponse,
    SurfaceConnectDescriptor,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
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


async def build_available_surfaces(
    *, connector_service: ConnectorService
) -> AvailableSurfacesResponse:
    """The connectable-surface catalog: one row per registry platform."""
    surfaces: list[AvailableSurface] = []
    for platform, binding in SURFACE_CONNECTOR_BINDINGS.items():
        connect, available, title, description, icon = await _connect_descriptor(
            connector_service, binding.connector_id
        )
        surfaces.append(
            AvailableSurface(
                platform=platform,
                connector_id=binding.connector_id,
                provider=binding.provider,
                title=title,
                description=description,
                icon=icon,
                supported_credential_modes=_supported_credential_modes(platform),
                connector_available=available,
                connect=connect,
            )
        )
    return AvailableSurfacesResponse(surfaces=surfaces)
