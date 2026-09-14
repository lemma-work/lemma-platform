"""Write-side enforcement of "one surface may claim an identity" rules.

Three rules, in widening scope. A connected account, and the Lemma-managed
identity for a platform, are each claimable once *per organization*.
``available_surfaces_builder`` reads that rule to disable an option before the
user picks it; this module is what refuses the write when two pods race for the
identity anyway.

The third is wider, because the thing it protects is. A bot the platform
delivers to -- a Slack app in a workspace, a Telegram bot behind its token --
answers for exactly one surface *everywhere*, since the platform routes to the
bot and has never heard of our organizations.

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


async def ensure_unique_platform_identity(
    surface: AgentSurfaceEntity,
    *,
    surface_repository: SurfaceInstallationRepositoryPort,
) -> None:
    """Refuse a surface that would answer as a bot another surface already is.

    The rule `PS-SURF-001` already states: "a surface shall answer as exactly
    one agent... where a person wants a second agent reachable on a platform,
    the system shall let them make that agent its own bot rather than sharing
    one." This is the half of it that was never enforced.

    ``ensure_unique_org_credential_binding`` cannot enforce it, because it
    compares ``account_id`` -- a row in *our* database. Connected accounts are
    per person (``accounts.user_id``), so two colleagues installing the same
    Slack app into the same workspace get two account rows with different ids
    and the same bot behind them. Nothing about that is a credential conflict;
    it is two pods claiming one identity, and inbound cannot tell them apart:
    the webhook is grouped by app id, both surfaces land in
    ``receiver_surface_ids``, both pass ``allows_inbound_event``, and routing
    settles it on creation order. The colleague who set theirs up second gets a
    surface that reads ACTIVE and never receives a message.

    Keyed on what the platform actually delivers to -- which workspace, and
    which bot in it -- rather than on a platform name. That is why there is no
    branch here: a surface that records both is one the platform routes by
    identity, and today Slack is the only one whose binding resolves both
    (``SurfaceAccountBindingResolver``). Telegram and WhatsApp record neither
    and are covered by their own account-level rule; Teams records a tenant but
    no bot, because its bot is the deployment's. Adding a platform that records
    both gets this rule for free, which is the intent.

    Missing fields are not a wildcard. A surface that never recorded who its bot
    is cannot be compared against one that did, so it is left alone rather than
    treated as matching everything -- the same reading ``routing_surfaces``
    takes of a NULL workspace, and the opposite of ``matches_tenant``, which
    answers a different question.
    """
    workspace_id = str(surface.external_workspace_id or "").strip()
    bot_identity = str(surface.surface_identity_id or "").strip()
    if not workspace_id or not bot_identity:
        return

    holder = await surface_repository.get_platform_identity_holder(
        pod_id=surface.pod_id,
        platform=surface.surface_type.value,
        external_workspace_id=workspace_id,
        surface_identity_id=bot_identity,
        exclude_surface_id=surface.id,
    )
    if holder is None:
        return

    platform_name = surface.surface_type.value.title()
    if holder.same_org:
        raise AgentSurfaceCredentialConflictError(
            f"This {platform_name} bot already answers for another surface in "
            "this organization. Delete that surface, or add a second bot, so "
            "each agent has an identity of its own.",
            pod_id=holder.surface.pod_id,
            surface_name=holder.surface.name,
            kind="IDENTITY",
        )
    # Another organization holds it. Naming their pod would tell this caller
    # something about a tenant they have no relationship with, so the refusal
    # says what is wrong and nothing about who.
    raise AgentSurfaceValidationError(
        f"This {platform_name} bot is already connected to Lemma elsewhere. A "
        "bot answers for one surface, so this one needs an app of its own in "
        f"the {platform_name} workspace."
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
