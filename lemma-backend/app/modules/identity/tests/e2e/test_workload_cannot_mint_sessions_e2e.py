"""A workload's token is never exchanged for a session of the person it runs for.

An agent or function acting for someone carries a delegated token: the person's
identity, narrowed to one pod and to the workload's grants. ``verify_auth``
admits that token on every route, so a route that reads only
``request.state.user`` treats the workload as the person. Three such routes
hand out credentials with none of the narrowing:

- ``/auth/cli/session-tokens`` returned a fresh access *and refresh* token;
- ``/auth/desktop/requests/{id}/complete`` approved a sign-in the workload had
  started itself, which ``/auth/desktop/session`` then turned into cookies;
- ``/oauth/consent/{id}`` let it connect an MCP client of its own choosing to
  any pod the person can read, for thirty days.

Code in an agent's sandbox holds such a token, so a prompt-injected agent run by
an organization owner could leave with that owner's account.
"""

from __future__ import annotations

import base64
import hashlib
from uuid import UUID, uuid4, uuid7

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e

_REFUSED = "WORKLOAD_NOT_ALLOWED"


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Workload sessions {uuid4().hex[:8]}",
            "description": "workload cannot mint sessions e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _workload_headers(
    *, user_id: str, pod_id: str, default_agent: bool
) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID if default_agent else uuid7(),
        workload_name=DEFAULT_POD_AGENT_NAME if default_agent else "helper",
        pod_id=UUID(pod_id),
        session_id=f"workload-sessions-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


def _challenge() -> str:
    verifier = "workload-verifier-" + uuid4().hex + uuid4().hex
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@pytest.fixture(params=[True, False], ids=["default-agent", "named-agent"])
async def workload_headers(
    request, authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
) -> dict[str, str]:
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    return await _workload_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=request.param
    )


@pytest.mark.asyncio
async def test_a_workload_cannot_mint_a_cli_session(
    async_client: AsyncClient, workload_headers: dict[str, str]
):
    response = await async_client.post(
        "/auth/cli/session-tokens", headers=workload_headers
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == _REFUSED
    assert "refresh_token" not in response.text


@pytest.mark.asyncio
async def test_a_person_still_mints_a_cli_session(authenticated_client: AsyncClient):
    response = await authenticated_client.post("/auth/cli/session-tokens")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["refresh_token"]


@pytest.mark.asyncio
async def test_a_workload_cannot_complete_a_desktop_sign_in(
    async_client: AsyncClient, workload_headers: dict[str, str]
):
    created = await async_client.post(
        "/auth/desktop/requests", json={"code_challenge": _challenge()}
    )
    assert created.status_code == status.HTTP_200_OK, created.text
    request_id = created.json()["request_id"]

    completed = await async_client.post(
        f"/auth/desktop/requests/{request_id}/complete", headers=workload_headers
    )

    assert completed.status_code == status.HTTP_403_FORBIDDEN, completed.text
    assert completed.json()["code"] == _REFUSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/oauth/consent/{id}", None),
        ("POST", "/oauth/consent/{id}", {"allow": True}),
        ("GET", "/oauth/grants", None),
        ("DELETE", "/oauth/grants/{id}", None),
    ],
)
async def test_a_workload_cannot_answer_or_manage_mcp_consent(
    async_client: AsyncClient,
    authenticated_client: AsyncClient,
    workload_headers: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
):
    url = path.format(id=uuid4())

    refused = await async_client.request(
        method, url, headers=workload_headers, json=body
    )
    as_person = await authenticated_client.request(method, url, json=body)

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == _REFUSED
    # The person reaches the route itself: what refused the workload is the
    # workload check, not a missing request.
    assert as_person.status_code != status.HTTP_403_FORBIDDEN, as_person.text
