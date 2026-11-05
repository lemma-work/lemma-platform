"""A retired or unknown model is refused when it is named, not swapped at run.

A teammate pinned to a model its provider no longer offers used to run -- and
be billed -- on the provider's default instead, with a log line as the only
trace; and nothing checked a runtime when it was saved. Over HTTP, against the
real repositories: a save naming an unknown model answers 422, and a turn on a
model removed since answers 422 before any run exists. A member's own runtime
is theirs to pin, on a teammate the pod sees too; anybody else reaching it is
told whose it is instead of being run on it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

# Nothing listens on the discard port, so discovery finds no models and the
# names given to the profile are its whole catalog.
_NOTHING_LISTENING = "http://127.0.0.1:9/v1"


async def _provider(
    client: AsyncClient, org_id: str, *, model_names: list[str]
) -> dict:
    response = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Provider {uuid4().hex[:8]}",
            "base_url": _NOTHING_LISTENING,
            "model_names": model_names,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


@pytest.mark.asyncio
async def test_saving_a_teammate_on_a_model_the_provider_does_not_offer_is_refused(
    authenticated_client: AsyncClient, fixed_test_org: dict
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    provider = await _provider(authenticated_client, org_id, model_names=["kept"])

    unknown = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"Pinned {uuid4().hex[:6]}",
            "instruction": "Answer.",
            "agent_runtime": {"profile_id": provider["id"], "model_name": "never"},
        },
    )
    assert unknown.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, unknown.text
    assert unknown.json()["code"] == "model_unavailable"
    # "never" was never offered, so nothing says it went away.
    assert "does not offer a model named never" in unknown.json()["message"]
    assert "no longer" not in unknown.json()["message"]
    assert "this teammate" in unknown.json()["message"]

    for_a_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": provider["id"], "model_name": "never"}},
    )
    assert for_a_conversation.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "this conversation" in for_a_conversation.json()["message"]
    assert "teammate" not in for_a_conversation.json()["message"]

    missing_profile = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": str(uuid4())}},
    )
    assert missing_profile.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert missing_profile.json()["code"] == "runtime_profile_unavailable"


@pytest.mark.asyncio
async def test_a_turn_on_a_model_removed_since_is_refused_before_a_run_exists(
    authenticated_client: AsyncClient, fixed_test_org: dict
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    provider = await _provider(
        authenticated_client, org_id, model_names=["kept", "retiring"]
    )
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_runtime": {"profile_id": provider["id"], "model_name": "retiring"}
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    retired = await authenticated_client.patch(
        f"/organizations/{org_id}/agent-runtime/profiles/{provider['id']}",
        json={"source": "OPENAI_COMPATIBLE", "model_names": ["kept"]},
    )
    assert retired.status_code == status.HTTP_200_OK, retired.text

    sent = await authenticated_client.post(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": "hello"},
    )
    assert sent.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, sent.text
    assert sent.json()["code"] == "model_unavailable"
    assert "retiring" in sent.json()["message"]

    detail = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert detail.status_code == status.HTTP_200_OK, detail.text
    assert detail.json()["last_run_status"] is None


async def _make_personal(profile_id: str, *, owner_id: str, first_name: str) -> None:
    """A member's own runtime, as an Agent Host profile is: personal, theirs.

    Written to the row because a personal profile is otherwise an Agent Host
    one, which needs a paired machine this test has no use for -- what is under
    test is whose the profile is, not what runs it.
    """
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE agent_runtime_profiles SET scope = 'PERSONAL', "
                "user_id = :owner WHERE id = :profile"
            ),
            {"owner": owner_id, "profile": profile_id},
        )
        await session.execute(
            text("UPDATE users SET first_name = :name WHERE id = :owner"),
            {"name": first_name, "owner": owner_id},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_member_pins_a_pod_teammate_to_their_own_runtime_and_others_are_told_whose(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org: dict,
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    async def _editor(slug: str) -> tuple[dict, dict[str, str]]:
        user = await signup_user(async_client, slug)
        member = await invite_org_member(
            authenticated_client, async_client, org_id=org_id, user=user
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=member["id"],
            role="POD_EDITOR",
        )
        return user, auth_headers(user)

    owner, as_owner = await _editor(f"runtime-owner-{uuid4().hex[:6]}")
    _peer, as_peer = await _editor(f"runtime-peer-{uuid4().hex[:6]}")
    provider = await _provider(authenticated_client, org_id, model_names=["kept"])
    await _make_personal(provider["id"], owner_id=owner["id"], first_name="Ada")
    pinned = {"profile_id": provider["id"], "model_name": "kept"}

    # The owner may put their own runtime to work on a teammate the pod sees.
    agent_name = f"pinned-{uuid4().hex[:6]}"
    saved = await async_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer.",
            "visibility": "POD",
            "agent_runtime": pinned,
        },
        headers=as_owner,
    )
    assert saved.status_code == status.HTTP_201_CREATED, saved.text

    # Anybody else is told whose it is, before a run exists -- never run on it.
    conversation = await async_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent_name},
        headers=as_peer,
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    sent = await async_client.post(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": "hello"},
        headers=as_peer,
    )
    assert sent.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, sent.text
    assert sent.json()["code"] == "runtime_profile_not_shareable"
    assert "Ada" in sent.json()["message"]
    assert provider["name"] in sent.json()["message"]
    detail = await async_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}", headers=as_peer
    )
    assert detail.json()["last_run_status"] is None

    # Nor may they pin a teammate of their own to it.
    borrowed = await async_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"borrowed-{uuid4().hex[:6]}",
            "instruction": "Answer.",
            "agent_runtime": pinned,
        },
        headers=as_peer,
    )
    assert borrowed.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert borrowed.json()["code"] == "runtime_profile_not_shareable"
