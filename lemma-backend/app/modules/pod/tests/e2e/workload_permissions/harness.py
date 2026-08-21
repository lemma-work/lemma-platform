"""Shared helpers for the holistic workload-permissions e2e suite.

This is a plain helper module (no pytest fixtures / no test collection) so the
folder/table/connector test files can share one source of truth for:

* creating pods, agents, and functions over HTTP,
* replacing workload (AGENT/FUNCTION) and ROLE resource grants over HTTP,
* minting a delegation token + httpx client for each workload flavour
  (named agent, function, and the DEFAULT POD AGENT "user-resolved" mode),
* building the in-process delegated-workload authorization context (for
  service-level connector tests), and
* seeding connectors / auth configs / connected accounts.

The three workload flavours under test:

* **named agent / function** — ``is_default_pod_agent=False``. Zero ambient
  access: every resource needs an explicit workload grant (grantee AGENT/FUNCTION).
* **default pod agent** ("user-resolved") — ``workload_id=DEFAULT_POD_AGENT_ID``,
  name ``pod_default``. Acts user-equivalent within the pod, mirroring the
  invoking user's pod permissions; needs NO per-resource workload grant.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.core.authorization.service import AuthorizationDataService
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

# Re-exported so test files import the datastore HTTP wrapper from one place.
from app.modules.datastore.tests.e2e.harness import DatastoreApi  # noqa: F401

AGENT = "agent"
FUNCTION = "function"


# --------------------------------------------------------------------------- #
# Pod / workload creation (HTTP, as the owner)
# --------------------------------------------------------------------------- #
async def create_pod(owner_client: AsyncClient, fixed_test_org: dict) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "name": f"wl-perms-{uuid4().hex[:8]}",
            "description": "workload permissions e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def create_agent(owner_client: AsyncClient, pod_id: str, name: str) -> dict:
    response = await owner_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def create_function(owner_client: AsyncClient, pod_id: str, name: str) -> dict:
    response = await owner_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "workload permissions e2e"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def create_workload(
    owner_client: AsyncClient, pod_id: str, workload_type: str, name: str
) -> dict:
    """Create an agent or a function and return its dict (``id``/``name``)."""
    if workload_type == AGENT:
        return await create_agent(owner_client, pod_id, name)
    if workload_type == FUNCTION:
        return await create_function(owner_client, pod_id, name)
    raise ValueError(f"Unknown workload_type: {workload_type}")


async def replace_workload_grants(
    owner_client: AsyncClient,
    pod_id: str,
    workload_type: str,
    name: str,
    grants: list[dict],
) -> None:
    collection = "agents" if workload_type == AGENT else "functions"
    response = await owner_client.put(
        f"/pods/{pod_id}/{collection}/{name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


async def replace_role_grants(
    owner_client: AsyncClient, pod_id: str, role_name: str, grants: list[dict]
) -> None:
    response = await owner_client.put(
        f"/pods/{pod_id}/roles/{role_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


# --------------------------------------------------------------------------- #
# Token / client minting
# --------------------------------------------------------------------------- #
async def mint_workload_token(
    *,
    user_id: str,
    workload_type: str,
    workload_id: str,
    pod_id: str,
    workload_name: str | None,
    scope: list[str] | None = None,
) -> str:
    claims = build_delegation_claims(
        workload_type=workload_type,
        workload_id=UUID(workload_id),
        pod_id=UUID(pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user_id),
        workload_name=workload_name,
        scope=scope,
    )
    return await get_user_token(UUID(user_id), delegation_claims=claims)


def _client_for_token(test_app, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


async def mint_workload_client(
    test_app,
    *,
    user_id: str,
    workload_type: str,
    workload_id: str,
    pod_id: str,
    workload_name: str | None,
    scope: list[str] | None = None,
) -> AsyncClient:
    """An httpx client authenticated as a named agent/function workload.

    No delegation scope is set by default (scope=[]), matching the in-process
    pod-tool path so resource grants are the sole limiter.
    """
    token = await mint_workload_token(
        user_id=user_id,
        workload_type=workload_type,
        workload_id=workload_id,
        pod_id=pod_id,
        workload_name=workload_name,
        scope=scope,
    )
    return _client_for_token(test_app, token)


async def mint_default_pod_agent_client(
    test_app, *, user_id: str, pod_id: str
) -> AsyncClient:
    """An httpx client for the DEFAULT POD AGENT (user-resolved mode).

    The middleware recognises ``workload_id == DEFAULT_POD_AGENT_ID`` +
    name ``pod_default`` and builds a user-equivalent, pod-clamped context.
    """
    token = await mint_workload_token(
        user_id=user_id,
        workload_type=AGENT,
        workload_id=str(DEFAULT_POD_AGENT_ID),
        pod_id=pod_id,
        workload_name=DEFAULT_POD_AGENT_NAME,
    )
    return _client_for_token(test_app, token)


# --------------------------------------------------------------------------- #
# In-process authorization context (service-level tests)
# --------------------------------------------------------------------------- #
async def build_workload_ctx(
    db_session,
    *,
    user_id: str,
    workload_type: str,
    workload_id: str,
    pod_id: str,
    workload_name: str | None,
    is_default_pod_agent: bool = False,
):
    """Build the delegated-workload context exactly like the in-process pod
    tools / connector operation service do (no delegation scope)."""
    return await AuthorizationDataService(db_session).build_delegated_workload_context(
        user_id=UUID(user_id),
        principal_type=workload_type.upper(),
        principal_id=UUID(workload_id),
        pod_id=UUID(pod_id),
        is_default_pod_agent=is_default_pod_agent,
        delegation_actor_name=workload_name,
    )


# --------------------------------------------------------------------------- #
# Connector / account seeding
# --------------------------------------------------------------------------- #
async def seed_connector(db_session, connector_id: str) -> str:
    from app.modules.connectors.infrastructure.models.connector import Connector

    if await db_session.get(Connector, connector_id) is None:
        db_session.add(
            Connector(
                id=connector_id,
                title=f"{connector_id} title",
                description="workload permissions e2e connector",
                kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
        await db_session.commit()
    return connector_id


async def seed_auth_config(
    db_session, *, organization_id: str, connector_id: str, name: str
) -> str:
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

    auth_config = AuthConfig(
        organization_id=UUID(organization_id),
        connector_id=connector_id,
        name=name,
    )
    db_session.add(auth_config)
    await db_session.commit()
    return str(auth_config.id)


async def seed_account(
    db_session,
    *,
    user_id: str,
    organization_id: str,
    auth_config_id: str,
    connector_id: str,
) -> str:
    """Create a connected account for ``user_id`` via the real repository (so
    credentials are encrypted the same way production writes them)."""
    from app.core.crypto import get_secret_cipher
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.domain.account import AccountEntity
    from app.modules.connectors.infrastructure.repositories.account_repository import (
        AccountRepository,
    )

    repo = AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    )
    entity = AccountEntity(
        user_id=UUID(user_id),
        organization_id=UUID(organization_id),
        auth_config_id=UUID(auth_config_id),
        connector_id=connector_id,
    )
    created = await repo.create(entity)
    await db_session.commit()
    return str(created.id)


def build_account_resolution_service(db_session):
    from app.core.crypto import get_secret_cipher
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.infrastructure.repositories.account_repository import (
        AccountRepository,
    )
    from app.modules.connectors.services.account_resolution_service import (
        AccountResolutionService,
    )
    from app.modules.pod.services.authorization_factory import (
        create_authorization_service,
    )

    uow = SqlAlchemyUnitOfWork(db_session)
    return AccountResolutionService(
        account_repository=AccountRepository(uow, encryption=get_secret_cipher()),
        authorization_service=create_authorization_service(uow),
    )
