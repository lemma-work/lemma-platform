"""Who may claim a Lemma-managed credential or a bot, and who claims nothing.

The rule had no tests, and it shipped applied to a platform it does not fit.
One system Resend surface anywhere in an organization refused every mailbox
after it — including further agents in the *same pod*, since the conflict query
does not exclude the surface's own pod. On dev that surfaced as a pod assistant
reporting "creating a mailbox for it failed" with no cause, because the log
field carrying the reason is stripped by the logging pipeline.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceCredentialConflictError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    PlatformIdentityHolder,
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    ensure_unique_org_credential_binding,
    ensure_unique_platform_identity,
)

pytestmark = pytest.mark.asyncio


def _surface(
    platform: SurfacePlatform,
    *,
    agent_id=None,
    holding: str | None = None,
) -> AgentSurfaceEntity:
    # A surface has exactly one owner. `agent_id=None` here means the caller
    # does not care which -- the pod's own assistant is the honest default, and
    # its row id is the pod's.
    #
    # `holding` is the identity this surface carries of its own: a pooled
    # WhatsApp number. Whether it has one is part of what this rule asks, so it
    # has to be sayable here.
    pod_id = uuid4()
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        agent_id=agent_id or pod_id,
        credential_mode=SurfaceCredentialMode.SYSTEM,
        surface_identity_id=holding,
    )


class _Repository:
    """Reports the same holder for whatever is asked, and records the asking."""

    def __init__(self, conflict=None):
        self._conflict = conflict
        self.system_lookups = 0

    async def get_system_credential_conflict_in_org(self, **_kwargs):
        self.system_lookups += 1
        return self._conflict

    async def get_account_conflict_in_org(self, **_kwargs):
        return None


async def test_the_shared_bot_is_claimable_once_per_organization():
    """The exemption that made this rule unreachable where it mattered most.

    WhatsApp and Telegram were both skipped, on the grounds that shared-bot
    routing authorizes the sender and the personal pod separately. But one bot
    is precisely the credential that *is* an identity: with two organizations
    holding it, an inbound message has no predictable answer to whose it is.

    Telegram alone now. WhatsApp left this rule when its numbers became a pool
    -- see the scenario below -- and the parametrize went with it, because a
    two-platform sweep over a rule that applies to one of them reads as coverage
    it no longer has.

    Onboarding still gives every personal pod its own shared surface. It writes
    through the repository and does not come through here, which is deliberate
    and written down where the exemption used to be.
    """
    platform = SurfacePlatform.TELEGRAM
    repository = _Repository(_surface(platform))

    with pytest.raises(AgentSurfaceCredentialConflictError, match="System"):
        await ensure_unique_org_credential_binding(
            _surface(platform), surface_repository=repository
        )

    assert repository.system_lookups == 1


async def test_a_pooled_number_is_not_claimed_by_the_whole_organization():
    """Exclusivity got finer, not weaker, and this is the difference.

    One number per deployment made "the WhatsApp credential" and "the WhatsApp
    identity" the same sentence, so a second surface in an organization really
    was a second claim on one thing. A pool separates them: the credential is
    the number's, and an organization holding two numbers is the feature.

    What replaced this is `uq_agent_org_whatsapp_number` -- one *number* per
    organization, enforced by a unique index where a race cannot get past it,
    rather than one *platform* per organization enforced by a read-then-write
    that could. Keeping this rule as well would refuse the second number the
    pool exists to hand out.

    A surface *holding a number* is what earns the exemption, and the test says
    so rather than leaving it implicit: the index that replaced this rule is
    partial on `surface_identity_id IS NOT NULL`, so a surface holding nothing
    is not covered by it -- see the scenario below.

    The lookup is not performed at all, for the same reason Resend's is not:
    there is nothing it could usefully answer.
    """
    repository = _Repository(_surface(SurfacePlatform.WHATSAPP, holding="pool-a"))

    await ensure_unique_org_credential_binding(
        _surface(SurfacePlatform.WHATSAPP, holding="pool-b"),
        surface_repository=repository,
    )

    assert repository.system_lookups == 0, (
        "the organization-wide claim was still consulted for a pooled number, "
        "so a second allocation would be refused before it reached the index "
        "that actually decides"
    )


async def test_email_is_exempt_because_its_credential_is_not_an_identity():
    """Every pod and agent gets its own address off the one API key.

    Inbound routes on ``surface_identity_email``, which carries a unique index —
    so another pod's mailbox existing is not a conflict, it is the design. The
    lookup is not even performed: there is nothing it could usefully answer.
    """
    repository = _Repository(_surface(SurfacePlatform.RESEND))

    await ensure_unique_org_credential_binding(
        _surface(SurfacePlatform.RESEND), surface_repository=repository
    )

    assert repository.system_lookups == 0


async def test_a_second_agent_in_one_pod_may_also_have_a_mailbox():
    """The half that made this fail even inside a single pod.

    ``get_system_credential_conflict_in_org`` filters on the organization and
    excludes only ``exclude_surface_id``, never the surface's own pod. So the
    pod's own first mailbox counted as a conflict against its second one, and a
    pod could never have both an assistant mailbox and a named agent's.
    """
    pod_id = uuid4()
    first = _surface(SurfacePlatform.RESEND)
    second = _surface(SurfacePlatform.RESEND, agent_id=uuid4())
    object.__setattr__(first, "pod_id", pod_id)
    object.__setattr__(second, "pod_id", pod_id)

    repository = _Repository(first)

    await ensure_unique_org_credential_binding(second, surface_repository=repository)


async def test_a_custom_credential_surface_is_not_subject_to_the_system_rule():
    """The rule is about the *shared* identity, not a connected account."""
    surface = _surface(SurfacePlatform.WHATSAPP)
    object.__setattr__(surface, "credential_mode", SurfaceCredentialMode.CUSTOM)
    repository = _Repository(_surface(SurfacePlatform.WHATSAPP))

    await ensure_unique_org_credential_binding(surface, surface_repository=repository)

    assert repository.system_lookups == 0


async def test_the_one_number_a_deployment_without_a_pool_has_is_still_claimed_once():
    """The exemption is about the number a surface holds, not about WhatsApp.

    A deployment that has added no pool rows gives its surfaces no number, so
    `surface_identity_id` is NULL and `uq_agent_org_whatsapp_number` -- partial
    on that column being present -- does not see them. Exempting them from this
    rule as well left the deployment's single shared number with nothing at all
    constraining it: two pods in one organization could each take it, and the
    first inbound message would have no answerable owner.

    That is every deployment running today, which is what makes this the case
    worth a test rather than the exotic one.
    """
    holder = _surface(SurfacePlatform.WHATSAPP)
    repository = _Repository(holder)

    with pytest.raises(AgentSurfaceCredentialConflictError) as refused:
        await ensure_unique_org_credential_binding(
            _surface(SurfacePlatform.WHATSAPP), surface_repository=repository
        )

    assert repository.system_lookups == 1
    assert refused.value.details["kind"] == "SYSTEM"
    assert refused.value.details["conflicting_surface"]["pod_id"] == str(holder.pod_id)


async def test_a_mailbox_is_exempt_even_with_no_address_yet():
    """Resend's exemption is unconditional, and stays that way.

    WhatsApp's became conditional on holding a number, and the two platforms
    share the branch -- so the risk of the change is that Resend quietly
    inherits the condition and the bug this rule already had comes back. Its
    identity is minted for every surface rather than drawn from finite
    inventory, so there is no state in which the deployment's key is the
    identity.
    """
    repository = _Repository(_surface(SurfacePlatform.RESEND))

    await ensure_unique_org_credential_binding(
        _surface(SurfacePlatform.RESEND), surface_repository=repository
    )

    assert repository.system_lookups == 0


def _slack_surface(
    *,
    workspace_id: str | None = "T_ACME",
    bot_identity: str | None = "U_LEMMABOT",
    account_id=None,
) -> AgentSurfaceEntity:
    """A Slack surface as `SurfaceAccountBindingResolver` leaves one.

    Both ids are populated on every surface it resolves -- the workspace from
    ``raw_response.team.id``, the bot from ``raw_response.bot_user_id``, which
    it refuses to create a surface without. The defaults are that shape; the
    parameters are for the rows that predate it.
    """
    pod_id = uuid4()
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name="slack",
        surface_type=SurfacePlatform.SLACK,
        config=SurfaceConfig(),
        agent_id=pod_id,
        credential_mode=SurfaceCredentialMode.CUSTOM,
        account_id=account_id or uuid4(),
        external_workspace_id=workspace_id,
        surface_identity_id=bot_identity,
    )


class _IdentityRepository:
    """Answers who holds a bot, and records what it was asked."""

    def __init__(self, holder: PlatformIdentityHolder | None = None):
        self._holder = holder
        self.calls: list[dict[str, object]] = []

    async def get_platform_identity_holder(self, **kwargs):
        self.calls.append(kwargs)
        return self._holder


async def test_a_colleague_may_not_connect_the_same_slack_bot():
    """The case the account rule cannot see.

    Connected accounts are per person, so two colleagues installing one Slack
    app into one workspace hold two account rows with different ids -- and one
    bot. Inbound is keyed on the bot, so the second surface would either take
    the first pod's messages or silently receive none.
    """
    holder = _slack_surface()
    repository = _IdentityRepository(
        PlatformIdentityHolder(surface=holder, same_org=True)
    )

    # Same workspace, same bot, a different connected account: exactly what a
    # second person in the organization produces.
    with pytest.raises(AgentSurfaceCredentialConflictError) as refused:
        await ensure_unique_platform_identity(
            _slack_surface(), surface_repository=repository
        )

    assert refused.value.details["kind"] == "IDENTITY"
    assert refused.value.details["conflicting_surface"]["pod_id"] == str(holder.pod_id)


async def test_a_holder_in_another_organization_is_refused_but_not_named():
    """A refusal is not a reason to hand over another tenant's pod.

    The rule reaches across organizations because the platform does -- but the
    holder's pod name and id belong to a tenant this caller has no relationship
    with, so the refusal says what is wrong and nothing about who.
    """
    holder = _slack_surface()
    repository = _IdentityRepository(
        PlatformIdentityHolder(surface=holder, same_org=False)
    )

    with pytest.raises(AgentSurfaceValidationError) as refused:
        await ensure_unique_platform_identity(
            _slack_surface(), surface_repository=repository
        )

    assert not isinstance(refused.value, AgentSurfaceCredentialConflictError)
    assert str(holder.pod_id) not in str(refused.value)


async def test_a_second_slack_app_in_one_workspace_is_allowed():
    """The escape hatch `PS-SURF-001` promises.

    "Where a person wants a second agent reachable on a platform, the system
    shall let them make that agent its own bot." A second Slack app in the same
    workspace is a different bot user, so it is a different identity and there
    is nothing to refuse -- which is the whole reason the key is the bot rather
    than the workspace.
    """
    repository = _IdentityRepository(None)

    await ensure_unique_platform_identity(
        _slack_surface(bot_identity="U_SECONDBOT"), surface_repository=repository
    )

    assert repository.calls[0]["surface_identity_id"] == "U_SECONDBOT"


async def test_a_surface_that_never_recorded_its_bot_is_left_alone():
    """Missing is not a wildcard.

    A row from before the resolver required ``bot_user_id`` cannot be compared
    against one that has it. Treating NULL as matching everything would make
    the first such row block every Slack surface in the deployment.
    """
    repository = _IdentityRepository(
        PlatformIdentityHolder(surface=_slack_surface(), same_org=True)
    )

    await ensure_unique_platform_identity(
        _slack_surface(bot_identity=None), surface_repository=repository
    )
    await ensure_unique_platform_identity(
        _slack_surface(workspace_id=None), surface_repository=repository
    )

    assert repository.calls == []


async def test_a_surface_does_not_conflict_with_itself():
    """Re-saving a surface re-runs the rule; it must not refuse its own row."""
    surface = _slack_surface()
    repository = _IdentityRepository(None)

    await ensure_unique_platform_identity(surface, surface_repository=repository)

    assert repository.calls[0]["exclude_surface_id"] == surface.id
