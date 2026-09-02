"""A person who leaves an organization stops acting in it.

Removing a member deletes the `organization_members` row and nothing else. The
connector account and the provider credential inside it stay exactly where they
were, so an agent or schedule pinned to that account with
`connector_account.use` went on acting as the departed person at the provider
-- reading their mail, writing under their name -- indefinitely.

Nobody could clear it up either. They can no longer reach their own account,
because every account endpoint requires org membership; and no admin can,
because `connector.account.list` is hard-filtered to the caller's own user id
and there is no org-wide account listing anywhere in the API. The only lever
was deleting the whole install, which takes every other member's account with
it.

Enforced at resolution rather than at removal: this is the moment that decides
whether the credential gets used, and it catches an account left behind by any
path, not only by the one removal route.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import AccountResolutionError
from app.modules.connectors.services.account_resolution_service import (
    AccountResolutionService,
)

pytestmark = pytest.mark.asyncio

ORG = uuid4()


def _account(user_id) -> AccountEntity:
    return AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG,
        auth_config_id=uuid4(),
        connector_id="gmail",
        is_default=True,
        status=AccountStatus.CONNECTED,
    )


def _service(*, is_member: bool) -> AccountResolutionService:
    access = AsyncMock()
    access.user_has_organization_role = AsyncMock(return_value=is_member)
    return AccountResolutionService(
        account_repository=AsyncMock(),
        organization_access=access,
    )


async def test_an_account_whose_owner_has_left_is_refused():
    service = _service(is_member=False)

    with pytest.raises(AccountResolutionError, match="no longer a member"):
        await service._assert_owner_is_still_a_member(_account(uuid4()), ORG)


async def test_an_account_whose_owner_is_still_here_is_allowed():
    service = _service(is_member=True)

    await service._assert_owner_is_still_a_member(_account(uuid4()), ORG)


async def test_membership_is_checked_for_the_account_owner_not_the_caller():
    """The caller is the workload's invoker; the credential belongs to whoever
    connected it. Checking the wrong one would let a departed person's account
    through whenever a current member triggered the workload -- which is every
    time, since a workload always has a live invoker."""
    owner = uuid4()
    service = _service(is_member=True)

    await service._assert_owner_is_still_a_member(_account(owner), ORG)

    called_with = service.organization_access.user_has_organization_role.await_args
    assert called_with.args[0] == owner


async def test_it_stays_out_of_the_way_when_there_is_no_organization_in_context():
    """Some resolution paths carry no org. Refusing there would break them, and
    a null org is not evidence of anything about membership."""
    service = _service(is_member=False)

    await service._assert_owner_is_still_a_member(_account(uuid4()), None)
