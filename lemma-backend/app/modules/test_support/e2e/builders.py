"""Scenario builders for self-documenting E2E tests."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from starlette import status

from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)


@dataclass(slots=True)
class E2EScenario:
    owner_client: AsyncClient
    async_client: AsyncClient
    owner_user: dict[str, str]
    organization: dict | None = None
    pod: dict | None = None

    @property
    def org_id(self) -> str:
        assert self.organization is not None
        return str(self.organization["id"])

    @property
    def pod_id(self) -> str:
        assert self.pod is not None
        return str(self.pod["id"])

    async def create_org(self, *, name_prefix: str = "Scenario Org") -> dict:
        response = await self.owner_client.post(
            "/organizations",
            json={"name": f"{name_prefix} {uuid4().hex[:8]}"},
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        self.organization = response.json()
        return self.organization

    async def create_pod(
        self,
        *,
        name_prefix: str = "Scenario Pod",
        pod_type: str = "HYBRID",
    ) -> dict:
        if self.organization is None:
            await self.create_org()
        response = await self.owner_client.post(
            "/pods",
            json={
                "organization_id": self.org_id,
                "name": f"{name_prefix} {uuid4().hex[:8]}",
                "type": pod_type,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        self.pod = response.json()
        return self.pod

    async def create_org_with_pod(
        self, *, name_prefix: str = "Scenario"
    ) -> "E2EScenario":
        await self.create_org(name_prefix=f"{name_prefix} Org")
        await self.create_pod(name_prefix=f"{name_prefix} Pod")
        return self

    async def create_user(self, prefix: str) -> dict[str, str]:
        return await signup_user(self.async_client, prefix)

    async def add_user_to_pod(
        self,
        *,
        user: dict[str, str],
        role: str = "POD_VIEWER",
        roles: list[str] | None = None,
    ) -> dict:
        org_member = await invite_org_member(
            self.owner_client,
            self.async_client,
            org_id=self.org_id,
            user=user,
        )
        return await add_pod_member(
            self.owner_client,
            pod_id=self.pod_id,
            organization_member_id=org_member["id"],
            role=role,
            roles=roles or [role],
        )

    async def create_agent(self, *, name: str | None = None) -> dict:
        response = await self.owner_client.post(
            f"/pods/{self.pod_id}/agents",
            json={
                "name": name or f"scenario-agent-{uuid4().hex[:8]}",
                "instruction": "Return a concise answer.",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        return response.json()

    async def delegated_agent_headers(
        self, *, user: dict[str, str], agent: dict
    ) -> dict[str, str]:
        claims = build_delegation_claims(
            workload_type="agent",
            workload_id=UUID(agent["id"]),
            workload_name=agent.get("name"),
            pod_id=UUID(self.pod_id),
            session_id=f"scenario-agent-{uuid4().hex}",
            invoked_by_user_id=UUID(user["id"]),
        )
        token = await get_user_token(UUID(user["id"]), delegation_claims=claims)
        return {"Authorization": f"Bearer {token}"}

    async def default_pod_agent_headers(
        self, *, user: dict[str, str], legacy_sentinel_id: bool = False
    ) -> dict[str, str]:
        """A token for the pod's own assistant, shaped like the mint site's.

        ``workload_id`` is the assistant's ``agents`` row id, which *is* its
        pod's -- what ``run_context_builder`` passes as ``agent.id``. Pass
        ``legacy_sentinel_id=True`` to exercise the retired shape, which signed
        tokens can still carry across a deploy.
        """
        claims = build_delegation_claims(
            workload_type="agent",
            workload_id=(
                DEFAULT_POD_AGENT_ID if legacy_sentinel_id else UUID(self.pod_id)
            ),
            workload_name=DEFAULT_POD_AGENT_NAME,
            pod_id=UUID(self.pod_id),
            session_id=f"default-pod-agent-{uuid4().hex}",
            invoked_by_user_id=UUID(user["id"]),
        )
        token = await get_user_token(UUID(user["id"]), delegation_claims=claims)
        return {"Authorization": f"Bearer {token}"}

    async def current_pod_member_id(self) -> str:
        response = await self.async_client.get(
            f"/pods/{self.pod_id}/members/lookup/by-user-id/{self.owner_user['id']}",
            headers=auth_headers(self.owner_user),
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return str(response.json()["pod_member_id"])

    async def replace_role_resource_grants(
        self,
        *,
        role_name: str,
        grants: list[dict],
    ) -> dict:
        response = await self.owner_client.put(
            f"/pods/{self.pod_id}/roles/{role_name}/permissions",
            json={"grants": grants},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def replace_agent_resource_grants(
        self,
        *,
        agent_name: str,
        grants: list[dict],
    ) -> dict:
        response = await self.owner_client.put(
            f"/pods/{self.pod_id}/agents/{agent_name}/permissions",
            json={"grants": grants},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()


@pytest_asyncio.fixture
async def scenario(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user: dict[str, str],
) -> E2EScenario:
    return E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=fixed_test_user,
    )
