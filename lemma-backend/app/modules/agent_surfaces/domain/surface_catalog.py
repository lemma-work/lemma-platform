"""The platforms a pod can connect, as the setup screen is shown them.

Built by `available_surfaces_builder` and served by the catalog endpoint; kept
out of `api/` so the service that builds it does not import the HTTP layer.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.connectors.contracts import AuthScheme


class SurfaceConnectDescriptor(BaseModel):
    """What the frontend needs to render the "connect an account" (CUSTOM) flow
    for a surface's connector — a slim projection of the connector's LEMMA
    capability. ``system_oauth_available`` means the platform supplies the OAuth
    app so the user connects without registering their own (distinct from whether
    a fully-managed SYSTEM bot exists — that's ``supported_credential_modes``)."""

    auth_scheme: AuthScheme
    auth_config_schema: dict[str, object] | None = None
    credential_schema: dict[str, object] | None = None
    system_oauth_available: bool = False
    supports_org_custom_oauth: bool = False


class SurfaceSystemClaim(BaseModel):
    """Whether this org can still put the platform's Lemma-managed bot/number
    behind a surface.

    The shared identity is claimable exactly once per organization, so the setup
    UI can render the option as unavailable *before* the user commits instead of
    discovering it as a failed save. ``claimed_by_pod_id`` is the pod holding the
    claim — always a pod in the caller's own org, so linking to it leaks nothing
    they can't already see."""

    available: bool
    claimed_by_pod_id: UUID | None = None
    claimed_by_surface_name: str | None = None


class SurfaceUnavailableReason(StrEnum):
    """Why a platform cannot be connected on this server right now.

    Published so a setup screen can say what to do instead of offering a
    Connect button whose only outcome is a refusal. Each value names the thing
    that is missing, not the setting that supplies it: the setting is the
    operator's word, and on Desktop the person reading is the operator but
    configures it through a form, not an environment variable.
    """

    # Nowhere on the internet for the platform to deliver to, and no pull
    # receiver for it in this runtime (WhatsApp and Teams never have one).
    NEEDS_PUBLIC_LINK = "NEEDS_PUBLIC_LINK"
    # Email: no inbound domain to give addresses out on.
    NEEDS_EMAIL_DOMAIN = "NEEDS_EMAIL_DOMAIN"


class AvailableSurface(BaseModel):
    """One connectable surface platform. ``supported_credential_modes`` is the
    single source of truth for how it can be set up: ``[CUSTOM]`` means an account
    must be connected; ``[CUSTOM, SYSTEM]`` means a Lemma-managed bot can also run
    with no account. The frontend derives ``account_needed = SYSTEM not in modes``
    and ``system_bot_available = SYSTEM in modes``."""

    platform: SurfacePlatform
    connector_id: str
    kind: str
    title: str | None = None
    description: str | None = None
    icon: str | None = None
    supported_credential_modes: list[SurfaceCredentialMode]
    connector_available: bool = True
    connect: SurfaceConnectDescriptor | None = None
    # Only meaningful when SYSTEM is in supported_credential_modes; None means
    # the platform has no Lemma-managed identity to claim in the first place.
    system_claim: SurfaceSystemClaim | None = None
    # Whether this deployment can provision a dedicated bot for the user through
    # a manager bot (Telegram). Lets the setup UI offer that path only where it
    # actually works, instead of failing once the user has committed to it.
    managed_setup_available: bool = False
    # Email only: the domain every managed address is minted under. Published
    # because an agent's address is decided by ``email_address_allocation`` from
    # its own name and the pod's — everything except this domain, which is
    # deployment configuration. With it the builder can show someone the address
    # their agent is about to get, instead of promising one.
    email_domain: str | None = None
    # Set when no credential mode can work here, whatever the modes above say:
    # the same check the create path refuses on, published ahead of the click.
    unavailable_reason: SurfaceUnavailableReason | None = None


class AvailableSurfacesResponse(BaseModel):
    surfaces: list[AvailableSurface] = Field(default_factory=list)
