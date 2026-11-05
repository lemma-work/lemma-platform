"""An agent reading strangers' words asks before it acts -- over the API.

A member's run in a group is shown what people outside the pod wrote there,
and its conversation is marked (``core.authorization.outside_text``). The
sandbox's ``lemma`` CLI calls the API with the run's delegated token, minted
before the run read anything; this is that path, end to end. Each test pins
one rule:

* in a marked conversation the agent's token is refused anything beyond reads
  and the pod's own record writes, with a code an approval answers, and the
  refusal is kept for the card that asks;
* the person's approval lets it through;
* a conversation that is not marked is not asked.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.authorization.outside_text import (
    OUTSIDE_TEXT_REQUIRES_APPROVAL,
    forget_outside_text,
    note_outside_text,
)
from app.core.authorization.session_approvals import (
    record_session_approval,
    revoke_session_approval,
    session_refusals,
)
from app.core.authorization.permissions import Permissions
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Outside text {uuid4().hex[:8]}",
            "description": "outside-text gate e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


def _table_body(name: str) -> dict[str, object]:
    return {
        "name": name,
        "primary_key_column": "id",
        "enable_rls": False,
        "visibility": "POD",
        "columns": [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "title", "type": "TEXT", "required": True},
        ],
    }


async def _agent_headers(*, user_id: str, pod_id: str, session_id: str) -> dict:
    """The pod's own assistant's token, as a run's sandbox carries it."""
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(pod_id),
        workload_name=DEFAULT_POD_AGENT_NAME,
        pod_id=UUID(pod_id),
        session_id=session_id,
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def marked_conversation():
    session_id = f"outside-text-e2e-{uuid4().hex}"
    await note_outside_text(session_id)
    yield session_id
    await forget_outside_text(session_id)


async def test_an_agent_reading_strangers_asks_before_it_changes_the_pod(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    marked_conversation: str,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    ledger = f"ledger_{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables", json=_table_body(ledger)
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    agent = await _agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id, session_id=marked_conversation
    )

    # The person's own "add this": a record write goes through unasked.
    added = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/{ledger}/records",
        json={"data": {"title": "breakfast 2248"}},
        headers=agent,
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text
    read = await async_client.get(
        f"/pods/{pod_id}/datastore/tables/{ledger}/records", headers=agent
    )
    assert read.status_code == status.HTTP_200_OK, read.text

    # Changing what the pod is waits for the person.
    refused = await async_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json=_table_body(f"other_{uuid4().hex[:8]}"),
        headers=agent,
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == OUTSIDE_TEXT_REQUIRES_APPROVAL
    # Kept for the card that asks, so the person's approval lends exactly it.
    pending = await session_refusals(
        session_id=marked_conversation,
        person_user_id=UUID(fixed_test_user["id"]),
        workload_actor_id=f"agent:{pod_id}",
    )
    assert Permissions.DATASTORE_TABLE_CREATE in pending


async def test_the_persons_approval_lets_the_agent_act(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    marked_conversation: str,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    agent = await _agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id, session_id=marked_conversation
    )
    approval = {
        "session_id": marked_conversation,
        "person_user_id": UUID(fixed_test_user["id"]),
        "workload_actor_id": f"agent:{pod_id}",
        "permission_id": Permissions.DATASTORE_TABLE_CREATE,
    }
    await record_session_approval(**approval)
    try:
        created = await async_client.post(
            f"/pods/{pod_id}/datastore/tables",
            json=_table_body(f"approved_{uuid4().hex[:8]}"),
            headers=agent,
        )
    finally:
        await revoke_session_approval(**approval)

    assert created.status_code == status.HTTP_201_CREATED, created.text


async def test_a_conversation_reading_nothing_from_outside_is_not_asked(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    agent = await _agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        session_id=f"outside-text-e2e-{uuid4().hex}",
    )

    created = await async_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json=_table_body(f"plain_{uuid4().hex[:8]}"),
        headers=agent,
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
