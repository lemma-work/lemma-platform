"""A person deletes their own account: they go, and the work they shared stays.

Through the real API, SuperTokens and the work runtime:

- someone alone in their workspace takes it with them, is signed out
  everywhere, and can sign up again with the same address as somebody new;
- a member of a team leaves it, and what they said there stays, under
  "Deleted user";
- the only owner of a team, a mistyped confirmation, and a billing provider
  that says no are each refused with nothing changed;
- a browser's cookie session deletes as a phone's bearer session does, and the
  cookie it still holds opens nothing afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.core.plan_limits import declare_plan_limits, reset_plan_limits
from app.modules.agent.infrastructure.runtime_models import AgentHostModel
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.identity.contracts.account_deletion import (
    AccountDeletionRefusal,
    configure_account_deletion_guard,
)
from app.modules.identity.infrastructure.models import (
    Organization,
    OrganizationMember,
    User,
)
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _delete(
    client: AsyncClient,
    confirm_email: str,
    headers: dict[str, str],
) -> Response:
    return await client.request(
        "DELETE", "/users/me", headers=headers, json={"confirm_email": confirm_email}
    )


async def _workspace_of(client: AsyncClient, person: dict[str, str]) -> tuple[str, str]:
    """An organization ``person`` alone is in, with one pod: (org id, pod id)."""
    as_person = auth_headers(person)
    organization = await client.post(
        "/organizations",
        headers=as_person,
        json={"name": f"Own {uuid4().hex[:8]}"},
    )
    assert organization.status_code == 201, organization.text
    org_id = organization.json()["id"]
    pod = await client.post(
        "/pods",
        headers=as_person,
        json={"organization_id": org_id, "name": f"Mine {uuid4().hex[:8]}"},
    )
    assert pod.status_code == 201, pod.text
    return org_id, pod.json()["id"]


async def _row[T](db_session: AsyncSession, model: type[T], id: str) -> T:
    db_session.expire_all()
    found = await db_session.get(model, UUID(id))
    assert found is not None
    return found


async def _member_ids(db_session: AsyncSession, org_id: str) -> set[str]:
    db_session.expire_all()
    rows = await db_session.scalars(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == UUID(org_id)
        )
    )
    return {str(user_id) for user_id in rows}


async def test_someone_alone_in_their_workspace_takes_it_with_their_account(
    async_client: AsyncClient, signup_user, db_session: AsyncSession
):
    person = await signup_user()
    org_id, pod_id = await _workspace_of(async_client, person)

    deleted = await _delete(async_client, person["email"], auth_headers(person))

    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "deleted": True,
        "organizations_left": 0,
        "organizations_deleted": 1,
        "pods_deleted": 1,
    }
    account = await _row(db_session, User, person["id"])
    assert account.is_deleted is True and account.is_active is False
    assert account.email == f"deleted-{person['id']}@deleted.example"
    assert (account.first_name, account.last_name) == ("Deleted", "user")
    assert (await _row(db_session, Pod, pod_id)).is_deleted is True
    workspace = await _row(db_session, Organization, org_id)
    assert workspace.name == "Deleted organization"
    assert await _member_ids(db_session, org_id) == set()

    # The token the phone still holds no longer opens anything.
    me = await async_client.get("/users/me", headers=auth_headers(person))
    assert me.status_code == 403, me.text
    assert me.json()["code"] == "ACCOUNT_INACTIVE"

    # The address is free once the deletion's work has run: signing up with
    # it is somebody new.
    await run_until_idle(async_session_maker, classes=[WorkClass.DELIVERY])
    again = await signup_user(email=person["email"])
    assert again["id"] != person["id"]
    fresh = await async_client.get("/users/me", headers=auth_headers(again))
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["email"] == person["email"]


async def test_a_member_who_deletes_their_account_leaves_their_words_with_the_team(
    scenario, db_session: AsyncSession
):
    await scenario.create_org_with_pod(name_prefix="Team")
    member = await scenario.create_user("leaving")
    organization_member = await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=member,
    )
    await add_pod_member(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        organization_member_id=organization_member["id"],
        role="POD_EDITOR",
    )
    as_member = auth_headers(member)
    channel = await scenario.async_client.post(
        f"/pods/{scenario.pod_id}/channels/me", headers=as_member
    )
    assert channel.status_code == 201, channel.text
    said = await scenario.async_client.post(
        f"/pods/{scenario.pod_id}/channels/{channel.json()['id']}/messages",
        headers=as_member,
        json={"text": "Handing this over before I go."},
    )
    assert said.status_code == 201, said.text

    deleted = await _delete(scenario.async_client, member["email"], as_member)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["organizations_left"] == 1
    assert deleted.json()["organizations_deleted"] == 0
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    assert await _member_ids(db_session, scenario.org_id) == {scenario.owner_user["id"]}
    assert (await _row(db_session, Pod, scenario.pod_id)).is_deleted is False
    team = await _row(db_session, Organization, scenario.org_id)
    assert team.name != "Deleted organization"
    line = await _row(db_session, ChannelMessageModel, said.json()["id"])
    assert line.body == "Handing this over before I go."
    assert str(line.sender_user_id) == member["id"]
    assert line.sender_name == "Deleted user"


async def test_a_deleted_accounts_paired_computer_stops_working_for_it(
    async_client: AsyncClient, signup_user, db_session: AsyncSession
):
    person = await signup_user()
    host = AgentHostModel(
        user_id=UUID(person["id"]),
        installation_id=f"install-{uuid4().hex}",
        host_secret_hash=uuid4().hex + uuid4().hex,
        display_name="Laptop",
        host_release="test",
    )
    db_session.add(host)
    await db_session.commit()

    deleted = await _delete(async_client, person["email"], auth_headers(person))
    assert deleted.status_code == 200, deleted.text
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    revoked = await _row(db_session, AgentHostModel, str(host.id))
    assert revoked.revoked_at is not None
    assert revoked.status == "REVOKED"


async def test_the_only_owner_of_a_team_is_told_to_hand_it_on_first(
    test_app, async_client: AsyncClient, signup_user, db_session: AsyncSession
):
    owner = await signup_user()
    org_id, pod_id = await _workspace_of(async_client, owner)
    colleague = await signup_user()
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers=auth_headers(owner),
    ) as owner_client:
        await invite_org_member(
            owner_client, async_client, org_id=org_id, user=colleague
        )
        name = (await owner_client.get(f"/organizations/{org_id}")).json()["name"]

    refused = await _delete(async_client, owner["email"], auth_headers(owner))

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "ACCOUNT_DELETION_SOLE_OWNER"
    assert f"Make someone else an owner of {name} first" in body["message"]
    assert body["details"] == {"organizations": [{"id": org_id, "name": name}]}
    assert (await _row(db_session, User, owner["id"])).is_deleted is False
    assert (await _row(db_session, Pod, pod_id)).is_deleted is False
    assert await _member_ids(db_session, org_id) == {owner["id"], colleague["id"]}
    me = await async_client.get("/users/me", headers=auth_headers(owner))
    assert me.status_code == 200, me.text


async def test_a_mistyped_confirmation_deletes_nothing(
    async_client: AsyncClient, signup_user, db_session: AsyncSession
):
    person = await signup_user()

    # An empty box is a mismatch too, with the same code, not a validation error.
    for typed in ("someone-else@example.com", ""):
        refused = await _delete(async_client, typed, auth_headers(person))

        assert refused.status_code == 400, refused.text
        assert refused.json()["code"] == "ACCOUNT_DELETION_CONFIRMATION_MISMATCH"
    assert (await _row(db_session, User, person["id"])).is_deleted is False


async def test_a_browser_session_deletes_and_its_cookie_opens_nothing_after(
    test_app, signup_user, db_session: AsyncSession
):
    person = await signup_user()
    # A client of its own: `async_client` may carry a bearer header, which
    # SuperTokens prefers over any cookie. `rid` is what the web SDK sends, and
    # what satisfies the anti-CSRF check for a cookie session.
    browser_cookie = {"Cookie": f"sAccessToken={person['token']}", "rid": "session"}
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as browser:
        deleted = await _delete(browser, person["email"], browser_cookie)
        after = await browser.get("/users/me", headers=browser_cookie)

    assert deleted.status_code == 200, deleted.text
    assert (await _row(db_session, User, person["id"])).is_deleted is True
    # The cookie the browser still holds is refused from the answer on.
    assert after.status_code == 403, after.text
    assert after.json()["code"] == "ACCOUNT_INACTIVE"


class _Refusing:
    async def refusal(
        self, *, user_id: UUID, deleted_organization_ids: Sequence[UUID]
    ) -> AccountDeletionRefusal | None:
        del user_id, deleted_organization_ids
        return AccountDeletionRefusal(
            code="ACCOUNT_DELETION_ACTIVE_SUBSCRIPTION",
            message="Cancel your subscription before deleting your account.",
        )


class _NothingLimited:
    """A plans provider that is present and limits nothing."""

    async def pod_allowance(self, *, user_id: UUID, organization_id: UUID) -> None:
        return None

    async def organization_limit(self, *, user_id: UUID) -> None:
        return None

    async def member_limit(self, *, organization_id: UUID) -> None:
        return None

    async def workspace_size(self, *, user_id: UUID) -> None:
        return None


@pytest.fixture
def billing() -> Iterator[None]:
    declare_plan_limits(lambda uow: _NothingLimited())
    yield
    configure_account_deletion_guard(None)
    reset_plan_limits()


async def test_what_billing_refuses_stops_the_deletion_with_nothing_changed(
    async_client: AsyncClient, signup_user, db_session: AsyncSession, billing
):
    person = await signup_user()
    org_id, pod_id = await _workspace_of(async_client, person)
    configure_account_deletion_guard(lambda uow: _Refusing())

    refused = await _delete(async_client, person["email"], auth_headers(person))

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "ACCOUNT_DELETION_ACTIVE_SUBSCRIPTION"
    assert refused.json()["message"] == (
        "Cancel your subscription before deleting your account."
    )
    assert (await _row(db_session, User, person["id"])).is_deleted is False
    assert (await _row(db_session, Pod, pod_id)).is_deleted is False
    assert await _member_ids(db_session, org_id) == {person["id"]}


async def test_a_deployment_that_bills_without_a_guard_refuses_rather_than_guess(
    async_client: AsyncClient, signup_user, db_session: AsyncSession, billing
):
    person = await signup_user()

    refused = await _delete(async_client, person["email"], auth_headers(person))

    assert refused.status_code == 503, refused.text
    assert refused.json()["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    assert (await _row(db_session, User, person["id"])).is_deleted is False
