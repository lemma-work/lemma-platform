"""`PUT /users/me/work`: what a person does, asked once and kept on them."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.identity.infrastructure.models.user_models import User
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_an_answer_is_kept_and_read_back_with_the_person(
    async_client, signup_user
):
    person = await signup_user()
    before = await async_client.get("/users/me", headers=auth_headers(person))
    assert before.status_code == 200, before.text
    assert before.json()["work"] is None, "never asked yet"

    answered = await async_client.put(
        "/users/me/work",
        headers=auth_headers(person),
        json={"jobs": ["SALES", "FOUNDER", "SALES"], "other": "  Podcasts  "},
    )
    assert answered.status_code == 200, answered.text
    work = answered.json()["work"]
    # Each job once, in the order given; what they typed, trimmed.
    assert work["jobs"] == ["SALES", "FOUNDER"]
    assert work["other"] == "Podcasts"
    assert work["answered_at"]
    assert answered.json()["id"] == person["id"]

    again = await async_client.get("/users/me", headers=auth_headers(person))
    assert again.status_code == 200, again.text
    assert again.json()["work"] == work


async def test_skipping_is_an_answer(async_client, signup_user):
    """Nothing picked and nothing typed still says they were asked."""
    person = await signup_user()

    skipped = await async_client.put(
        "/users/me/work",
        headers=auth_headers(person),
        json={"jobs": [], "other": "   "},
    )
    assert skipped.status_code == 200, skipped.text
    work = skipped.json()["work"]
    assert work["jobs"] == []
    assert work["other"] is None
    assert work["answered_at"]


async def test_answering_leaves_every_other_preference_alone(
    async_client, signup_user, db_session
):
    """Only the one key is written, so a default chat or a Home mark stored
    beside it survives the answer."""
    person = await signup_user()
    user_id = UUID(person["id"])
    surface_id = str(uuid4())
    organization_id = str(uuid4())
    user = await db_session.get(User, user_id)
    assert user is not None
    user.preferences = {
        "default_surfaces": {"WHATSAPP": surface_id},
        "home_seen": {organization_id: "2030-01-02T03:04:05+00:00"},
    }
    await db_session.commit()

    answered = await async_client.put(
        "/users/me/work",
        headers=auth_headers(person),
        json={"jobs": ["ENGINEERING"]},
    )
    assert answered.status_code == 200, answered.text

    db_session.expire_all()
    stored = await db_session.scalar(select(User.preferences).where(User.id == user_id))
    assert stored["default_surfaces"] == {"WHATSAPP": surface_id}
    assert stored["home_seen"] == {organization_id: "2030-01-02T03:04:05+00:00"}
    assert stored["work"]["jobs"] == ["ENGINEERING"]


@pytest.mark.parametrize(
    "body",
    [
        {"jobs": ["ASTRONAUT"]},
        {"jobs": [], "other": "x" * 121},
        {"other": "Podcasts"},
    ],
    ids=["unknown job", "too long", "no jobs list"],
)
async def test_an_answer_it_cannot_keep_is_refused(async_client, signup_user, body):
    person = await signup_user()
    refused = await async_client.put(
        "/users/me/work", headers=auth_headers(person), json=body
    )
    assert refused.status_code == 422, refused.text


async def test_it_needs_a_session(async_client):
    response = await async_client.put("/users/me/work", json={"jobs": []})
    assert response.status_code in (401, 403), response.text


async def test_colleagues_are_not_shown_it(async_client, signup_user):
    """What somebody does is on their own record only: a member list
    describes the person and leaves it out."""
    person = await signup_user()
    answered = await async_client.put(
        "/users/me/work", headers=auth_headers(person), json={"jobs": ["SALES"]}
    )
    assert answered.status_code == 200, answered.text
    workspace = await async_client.post(
        "/users/me/first-workspace", headers=auth_headers(person), json={}
    )
    assert workspace.status_code == 200, workspace.text

    members = await async_client.get(
        f"/organizations/{workspace.json()['organization_id']}/members",
        headers=auth_headers(person),
    )
    assert members.status_code == 200, members.text
    listed = [row["user"] for row in members.json()["items"] if row.get("user")]
    assert listed, "the member list describes its people"
    assert all(user.get("work") is None for user in listed)
