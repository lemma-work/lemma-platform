"""Write-side enforcement of the surface uniqueness rules.

A connected account, and the Lemma-managed identity for a platform, are each
claimable once per organization. ``available_surfaces_builder`` reads the same
rule to disable an option before the user picks it; this module is what refuses
the write when two pods race for the identity anyway.

And one agent reaches a platform in one place. That one is a database
constraint as well, so what this adds is a refusal a person can read instead of
an IntegrityError that arrives too late to do anything with.

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
    AgentSurfaceAgentPlatformConflictError,
    AgentSurfaceCredentialConflictError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    system_credential_claim_applies,
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

    # WhatsApp and Telegram were exempt here, on the grounds that shared-bot
    # routing authorizes the sender and the personal pod separately. The
    # exemption covered the two platforms whose system credential is most
    # plainly an identity -- one number, one bot -- and so made the branch below
    # unreachable for exactly the cases it was written for. With two
    # organizations each holding the shared number, an inbound message has no
    # predictable answer to "whose", which is the thing worth refusing.
    #
    # `onboarding_workspace._ensure_shared_surface` writes through
    # `SurfaceRepository.create` and does not come through here. That bypass is
    # deliberate and stays for now: every personal pod needs its own system
    # surface for routing to reach it, so "once per organization" applied there
    # would refuse the second person in a domain-join organization a workspace
    # at all. One organization per number is a rule for pool allocation, which
    # is where the pool will be. What this binds is the API and bundle paths --
    # the ones somebody drives on purpose.

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
    #
    # WhatsApp fails it *conditionally*, which is why the identity this surface
    # holds is part of the question. A surface holding a pooled number has its
    # own identity and `uq_agent_org_whatsapp_number` keeps it exclusive. A
    # surface holding none — every WhatsApp surface in a deployment that owns no
    # pool — is on the single number in settings, and that index is partial on
    # `surface_identity_id IS NOT NULL`, so it does not constrain it. Exempting
    # those too left the shared number claimable by every pod in an
    # organization, with nothing at all to say otherwise.
    if not system_credential_claim_applies(
        surface.surface_type.value,
        holds_own_identity=bool(surface.surface_identity_id),
    ):
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


async def ensure_one_surface_per_agent(
    surface: AgentSurfaceEntity,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
) -> None:
    """An agent reaches a platform in exactly one place.

    One Slack app, one WhatsApp number, one Telegram bot. The WhatsApp numbers
    come from a pool and each surface takes one, so an agent quietly holding two
    is an agent holding two of a scarce thing -- which is why the rule is broad
    rather than limited to the system-credential case.

    Scoped to the agent's own pod because a surface's ``pod_id`` is its agent's:
    the column is a routing scope, not a second owner.
    """
    existing, _ = await surface_repository.list_by_pod(
        surface.pod_id,
        platform=surface.surface_type.value,
        agent_id=surface.agent_id,
        match_agent=True,
    )
    conflict = next((item for item in existing if item.id != surface.id), None)
    if conflict is None:
        return
    raise AgentSurfaceAgentPlatformConflictError(
        platform=surface.surface_type.value,
        pod_id=conflict.pod_id,
        surface_name=conflict.name,
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
