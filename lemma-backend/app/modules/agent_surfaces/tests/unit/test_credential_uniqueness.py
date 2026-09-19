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
from app.modules.agent_surfaces.domain.ports import PlatformIdentityHolder
from app.modules.agent_surfaces.services.credential_uniqueness import (
    ensure_unique_org_credential_binding,
    ensure_unique_platform_identity,
)

pytestmark = pytest.mark.asyncio


def _surface(platform: SurfacePlatform, *, agent_id=None) -> AgentSurfaceEntity:
    # A surface has exactly one owner. `agent_id=None` here means the caller
    # does not care which -- the pod's own assistant is the honest default, and
    # its row id is the pod's.
    pod_id = uuid4()
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        agent_id=agent_id or pod_id,
        credential_mode=SurfaceCredentialMode.SYSTEM,
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


async def test_a_second_pod_may_not_take_the_whatsapp_number():
    """The rule the exemption must not weaken.

    Inbound WhatsApp arrives keyed on the number and nothing else, so two pods
    holding it would receive each other's messages.
    """
    # A real entity, because the guard checks isinstance before refusing — a
    # stand-in would make this pass by not being recognised as a conflict.
    holder = _surface(SurfacePlatform.WHATSAPP)

    with pytest.raises(AgentSurfaceCredentialConflictError):
        await ensure_unique_org_credential_binding(
            _surface(SurfacePlatform.WHATSAPP),
            surface_repository=_Repository(holder),
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
