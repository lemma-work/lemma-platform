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

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import reveal_secret
from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import (
    AvailableSurface,
    AvailableSurfacesResponse,
    SurfaceConnectDescriptor,
    SurfaceSystemClaim,
    SurfaceUnavailableReason,
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
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.agent_surfaces.platforms.common import (
    public_https_api_url_available,
    receives_without_public_link,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    system_credential_claim_applies,
)
from app.modules.connectors.contracts.surfaces import SurfaceConnector

logger = get_logger(__name__)

# Reads one connector out of the catalog. Bound to a unit of work by the caller,
# so this module never holds one; ``None`` means the connector is not catalogued.
ReadConnector = Callable[[str], Awaitable[SurfaceConnector | None]]


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
    read_connector: ReadConnector, connector_id: str
) -> tuple[SurfaceConnectDescriptor | None, bool, str | None, str | None, str | None]:
    """Resolve the connector's LEMMA capability into a connect descriptor plus its
    catalog display fields. Returns ``(descriptor, available, title, description,
    icon)``; ``available`` is False (and descriptor None) when the connector is
    missing, inactive, or exposes no LEMMA capability — so a mis-configured or
    not-yet-catalogued surface degrades to a visible "unavailable" row instead of
    500-ing the whole endpoint."""
    connector = await read_connector(connector_id)
    if connector is None:
        return None, False, None, None, None
    if not connector.is_active or connector.connect is None:
        if connector.connect is None:
            logger.debug(
                "agent_surfaces.available_surfaces_builder.surface_connector_s_has_no.diagnostic",
                connector_id=connector_id,
            )
        return None, False, connector.title, connector.description, connector.icon

    descriptor = SurfaceConnectDescriptor(
        auth_scheme=connector.connect.auth_scheme,
        auth_config_schema=connector.connect.auth_config_schema,
        credential_schema=connector.connect.credential_schema,
        system_oauth_available=connector.connect.system_oauth_available,
        supports_org_custom_oauth=connector.connect.supports_org_custom_oauth,
    )
    return descriptor, True, connector.title, connector.description, connector.icon


async def _would_hold_its_own_identity(
    platform: SurfacePlatform,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
) -> bool:
    """Would a surface created here and now get an identity of its own?

    The write side asks this of a surface it is holding; the catalog has to ask
    it of a surface that does not exist yet, which for WhatsApp means asking
    whether there is a pool to draw one from — the same question
    ``_wants_a_pooled_number`` asks a moment later when the person actually
    clicks. Every other platform's answer is a constant.

    Best-effort like the conflict read below it, and False on a failure: that
    keeps the claim rule applying, so a catalog that could not read the pool
    greys an option out rather than offering one that then fails to save.
    """
    if platform is not SurfacePlatform.WHATSAPP:
        return True
    try:
        return await WhatsAppNumberRepository(surface_repository.uow).any_allocatable()
    except SQLAlchemyError:
        logger.debug(
            "agent_surfaces.available_surfaces_builder.pool_lookup_failed.diagnostic",
            platform=platform.value,
        )
        return False


async def _system_claim(
    platform: SurfacePlatform,
    *,
    modes: list[SurfaceCredentialMode],
    pod_id: UUID | None,
    surface_repository: SurfaceInstallationRepositoryPort | None,
) -> SurfaceSystemClaim | None:
    """Who, if anyone, already holds this platform's Lemma-managed identity.

    The shared bot/number is claimable once per organization (enforced on write
    by ``ensure_unique_org_credential_binding``); returning it here lets the
    setup UI disable the option up front rather than surfacing a failed save.
    Only meaningful when the platform actually has a SYSTEM mode; best-effort,
    because a catalog read must not fail on a repository hiccup.

    Nothing to claim when the surface will have an identity of its own — Resend
    hands every pod and every agent its own address off one key, and WhatsApp
    hands out a number per surface wherever a pool exists. This mirrors the
    write-side exemption on purpose, condition for condition: a catalog that
    disagrees with the writer either offers something that then fails, or hides
    something that would have worked. Where there is no pool, WhatsApp is back
    to one number for the whole deployment and the claim applies again — which
    is the case this branch used to get wrong in the permissive direction, so
    the UI offered the shared number to every pod in an organization."""
    if SurfaceCredentialMode.SYSTEM not in modes:
        return None
    if pod_id is None or surface_repository is None:
        return SurfaceSystemClaim(available=True)
    if not system_credential_claim_applies(
        platform.value,
        holds_own_identity=await _would_hold_its_own_identity(
            platform, surface_repository=surface_repository
        ),
    ):
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
        reveal_secret(surface_settings.telegram_manager_bot_token)
        and surface_settings.telegram_manager_bot_username
    )


def _email_domain(
    platform: SurfacePlatform, *, modes: list[SurfaceCredentialMode]
) -> str | None:
    """The domain this deployment mints managed addresses under, or None.

    Only Resend has one: it is the platform where Lemma *owns* the mailbox, so
    an address exists for an agent that connected nothing. Gated on SYSTEM being
    a supported mode for the same reason ``email_is_configured`` is — without the
    key there is no address, and a domain published anyway would have the builder
    promise one that never arrives.
    """
    if platform is not SurfacePlatform.RESEND:
        return None
    if SurfaceCredentialMode.SYSTEM not in modes:
        return None
    return surface_settings.resend_inbound_domain or None


def unavailable_reason(
    platform: SurfacePlatform,
    *,
    public_link: bool,
    inbound_domain: bool,
    pulls: bool,
) -> SurfaceUnavailableReason | None:
    """What stops any surface of this platform being created here, if anything.

    Mirrors the two refusals on the write path, condition for condition:
    ``create_surface_on_minted_address`` needs an inbound domain before it can
    mint an address, whether the key is Lemma's or the person's own, and
    ``_validate_runtime_supported`` needs a public link unless a pull receiver
    runs for the platform (``pulls``). A catalog that disagreed would offer a
    Connect button that could only fail -- and on the credential path, fail
    after an account had already been made. Pure, so the rule is asserted
    without a deployment.
    """
    if platform is SurfacePlatform.RESEND and not inbound_domain:
        return SurfaceUnavailableReason.NEEDS_EMAIL_DOMAIN
    if not public_link and not pulls:
        return SurfaceUnavailableReason.NEEDS_PUBLIC_LINK
    return None


async def build_available_surfaces(
    *,
    read_connector: ReadConnector,
    pod_id: UUID | None = None,
    surface_repository: SurfaceInstallationRepositoryPort | None = None,
    has_public_link: Callable[[], bool] = public_https_api_url_available,
) -> AvailableSurfacesResponse:
    """The connectable-surface catalog: one row per registry platform.

    ``pod_id``/``surface_repository`` are optional so the catalog stays usable as
    a pure registry join; supply both to also resolve each platform's
    system-identity claim for the pod's organization. ``has_public_link`` is
    the deployment's answer to "can a webhook reach us", a seam so the rows
    that depend on it can be asserted without a deployment."""
    surfaces: list[AvailableSurface] = []
    public_link = has_public_link()
    for platform, binding in SURFACE_CONNECTOR_BINDINGS.items():
        connect, available, title, description, icon = await _connect_descriptor(
            read_connector, binding.connector_id
        )
        modes = _supported_credential_modes(platform)
        surfaces.append(
            AvailableSurface(
                platform=platform,
                connector_id=binding.connector_id,
                kind=binding.kind,
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
                email_domain=_email_domain(platform, modes=modes),
                unavailable_reason=unavailable_reason(
                    platform,
                    public_link=public_link,
                    inbound_domain=bool(surface_settings.resend_inbound_domain),
                    pulls=receives_without_public_link(platform),
                ),
            )
        )
    return AvailableSurfacesResponse(surfaces=surfaces)
