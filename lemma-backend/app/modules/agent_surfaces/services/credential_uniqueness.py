"""Write-side enforcement of "one surface may claim a credential" rules.

A connected account, and the Lemma-managed identity for a platform, are each
claimable once per organization. ``available_surfaces_builder`` reads the same
rule to disable an option before the user picks it; this module is what refuses
the write when two pods race for the identity anyway.

Kept beside the service rather than inside it: these are pure policy over the
repository, with no surface state, no runtime, and no side effects — and the
service is already the largest file in the module.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceCredentialConflictError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)


async def ensure_unique_org_credential_binding(
    surface: AgentSurfaceEntity,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
) -> None:
    """Refuse a surface that reuses another surface's credential in the org.

    Raises ``AgentSurfaceCredentialConflictError``, which carries the holder's
    pod and surface name so the UI can name who has it rather than showing a
    bare failed save.
    """
    if surface.account_id is not None:
        conflict = await surface_repository.get_account_conflict_in_org(
            pod_id=surface.pod_id,
            account_id=surface.account_id,
            exclude_surface_id=surface.id,
        )
        if isinstance(conflict, AgentSurfaceEntity):
            raise AgentSurfaceCredentialConflictError(
                "This connected account is already used by another surface in "
                "this organization. Delete that surface before reusing the account.",
                pod_id=conflict.pod_id,
                surface_name=conflict.name,
                kind="ACCOUNT",
            )
        return

    if surface.credential_mode is not SurfaceCredentialMode.SYSTEM:
        return

    # Only when the system credential *is* an identity. One Slack app, one
    # Telegram bot, one WhatsApp number: inbound arrives keyed on that identity
    # and nothing else, so a second pod claiming it would receive the first
    # pod's messages.
    #
    # Resend fails that premise. Its system credential is an API key over a
    # catch-all domain, and inbound routes on the surface's own unique
    # `surface_identity_email` — one address per pod, and one per agent, off the
    # single key. Applying the identity rule to it meant the first mailbox
    # created in an organization silently blocked every mailbox after it,
    # including further agents in the same pod, since this query does not
    # exclude the surface's own pod either.
    capabilities = get_platform_capabilities(surface.surface_type.value)
    if capabilities is not None and not capabilities.system_credential_is_identity:
        return

    conflict = await surface_repository.get_system_credential_conflict_in_org(
        pod_id=surface.pod_id,
        platform=surface.surface_type.value,
        exclude_surface_id=surface.id,
    )
    if isinstance(conflict, AgentSurfaceEntity):
        raise AgentSurfaceCredentialConflictError(
            f"System {surface.surface_type.value} credentials are already used "
            "by another surface in this organization. Delete that surface before "
            "enabling system credentials for another pod.",
            pod_id=conflict.pod_id,
            surface_name=conflict.name,
            kind="SYSTEM",
        )


async def ensure_unique_telegram_account(
    surface: AgentSurfaceEntity,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
) -> None:
    """A Telegram bot answers for exactly one surface, across all orgs.

    Telegram delivers by bot token, so a second surface on the same account
    would receive the first one's updates.
    """
    if surface.account_id is None:
        return
    existing = await surface_repository.get_by_platform_and_account_id(
        platform=SurfacePlatform.TELEGRAM.value,
        account_id=surface.account_id,
        exclude_surface_id=surface.id,
    )
    if existing is not None:
        raise AgentSurfaceValidationError(
            "Telegram account is already connected to another surface"
        )
