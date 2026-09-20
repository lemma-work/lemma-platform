"""Who may claim a Lemma-managed credential, and who is not claiming anything.

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
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    ensure_unique_org_credential_binding,
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

    The lookup is not performed at all, for the same reason Resend's is not:
    there is nothing it could usefully answer.
    """
    repository = _Repository(_surface(SurfacePlatform.WHATSAPP))

    await ensure_unique_org_credential_binding(
        _surface(SurfacePlatform.WHATSAPP), surface_repository=repository
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
