"""The organization role bound is derived from the role catalog, not restated.

``can_grant_org_role`` used to be a rank cap that let only owners hand out
anything above member. It is a comparison of permission sets now, so these tests
pin the two facts that matter: the bound follows what the roles *carry*, and the
role a request is judged by is the one the catalog says.
"""

from __future__ import annotations

import pytest

from app.core.authorization.permissions import Permissions
from app.modules.identity.domain.organization_entities import (
    OrganizationRole,
    can_act_on_org_member,
    can_grant_org_role,
    org_role_holds,
    org_role_permission_ids,
)

MEMBER, EDITOR, OWNER = (
    OrganizationRole.ORG_MEMBER,
    OrganizationRole.ORG_EDITOR,
    OrganizationRole.ORG_OWNER,
)


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        (OWNER, OWNER, True),
        (OWNER, EDITOR, True),
        (OWNER, MEMBER, True),
        (EDITOR, OWNER, False),
        (EDITOR, EDITOR, True),
        (EDITOR, MEMBER, True),
        (MEMBER, OWNER, False),
        (MEMBER, EDITOR, False),
        (MEMBER, MEMBER, True),
    ],
)
def test_a_role_may_confer_only_what_it_carries(actor, target, allowed):
    assert can_grant_org_role(actor, target) is allowed


@pytest.mark.parametrize("actor", list(OrganizationRole))
@pytest.mark.parametrize("member", list(OrganizationRole))
def test_reaching_over_a_member_is_the_same_question_as_granting_their_role(
    actor, member
):
    assert can_act_on_org_member(actor, member) is can_grant_org_role(actor, member)


def test_managing_people_is_an_editor_permission_and_billing_is_not():
    """The catalog is the source, so the roles agree with what it advertises."""
    for permission in (
        Permissions.ORG_MEMBER_MANAGE,
        Permissions.ORG_INVITATION_MANAGE,
    ):
        assert org_role_holds(EDITOR, permission)
        assert org_role_holds(OWNER, permission)
        assert not org_role_holds(MEMBER, permission)
    assert org_role_holds(OWNER, Permissions.ORG_BILLING_MANAGE)
    assert not org_role_holds(EDITOR, Permissions.ORG_BILLING_MANAGE)


def test_an_editor_lacks_exactly_what_makes_an_owner():
    assert org_role_permission_ids(OWNER) - org_role_permission_ids(EDITOR) == {
        Permissions.ORG_BILLING_MANAGE
    }


@pytest.mark.asyncio
async def test_an_invitation_naming_a_pod_is_refused_when_the_pod_cannot_be_checked():
    """Fail closed: unchecked, its pod grant would be honoured on acceptance."""
    from uuid import uuid4

    from app.modules.identity.domain.errors import IdentityConflictError
    from app.modules.identity.domain.organization_entities import (
        OrganizationInvitationEntity,
        OrganizationMemberEntity,
    )
    from app.modules.identity.services.membership_rules import resolve_invited_pod

    org_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+x@example.com",
        organization_id=org_id,
        role=MEMBER,
        pod_id=uuid4(),
        pod_role="POD_ADMIN",
    )
    inviter = OrganizationMemberEntity(
        user_id=uuid4(), organization_id=org_id, role=EDITOR
    )

    with pytest.raises(IdentityConflictError):
        await resolve_invited_pod(
            pod_membership_port=None, invitation=invitation, inviter=inviter
        )
    invitation.pod_id = None
    assert await resolve_invited_pod(
        pod_membership_port=None, invitation=invitation, inviter=inviter
    ) == (None, None)
