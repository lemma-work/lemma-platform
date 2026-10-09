"""A private app opened inside an AI tool, framed in someone else's page.

There the app host's normal cookie (``SameSite=Lax``) is never sent, so the
Lemma view mints a ticket from the AI tool's connection and the app redeems it
for a cookie made for the frame. These check that cookie's shape, that it
cannot stand in for the normal one or the other way round, and that it ends
when the connection does -- whichever way the connection ends.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.apps.contracts.embedded_access import mint_embedded_app_ticket
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.services.app_access import (
    AppAccessPurpose,
    verify_app_access_token,
)
from app.modules.apps.tests.e2e.test_app_access_security import (
    ACCESS_COOKIE,
    CONTENT,
    assert_gate,
    browser,
    check_every_request,
    hosted_app,
    ticket,
)
from app.modules.identity.contracts.delegated_tokens import mint_pod_agent_session
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.test_support.e2e_authz import signup_user

pytestmark = pytest.mark.e2e

__all__ = ["browser", "check_every_request", "hosted_app"]


async def _person_id(client: AsyncClient) -> UUID:
    me = await client.get("/users/me")
    assert me.status_code == 200, me.text
    return UUID(me.json()["id"])


async def _connection(db_manager, *, user_id: UUID, pod_id: str) -> UUID:
    """A live MCP connection to the pod, as consent leaves one."""
    now = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        repository = McpAccessRepository(SqlAlchemyUnitOfWork(session))
        await repository.save_client(
            client_id="https://chatgpt.example.test/oauth/client.json",
            registration="metadata_document",
            client_metadata={"client_name": "An AI tool"},
            client_secret_hash=None,
            now=now,
        )
        grant_id = await repository.create_grant(
            user_id=user_id,
            client_id="https://chatgpt.example.test/oauth/client.json",
            pod_id=UUID(pod_id),
            scopes=["pod:read", "pod:write"],
            resource=f"https://api.example.test/mcp/{pod_id}",
        )
        await session.commit()
    return grant_id


async def _embedded_ticket(
    db_manager, *, user_id: UUID, hosted_app, grant_id: UUID
) -> str:
    session = await mint_pod_agent_session(
        user_id=user_id, pod_id=UUID(hosted_app.pod_id), session_id=f"mcp:{grant_id}"
    )
    issued = await mint_embedded_app_ticket(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        slug=hosted_app.name,
        user_id=user_id,
        session_handle=session.session_handle,
        grant_id=grant_id,
    )
    assert issued.origin == hosted_app.origin
    return issued.ticket


async def _redeem(browser, hosted_app, value: str, *, embedded: bool):
    return await browser.post(
        hosted_app.origin + "/_lemma/app-access/redeem",
        headers={"Origin": hosted_app.origin},
        json={"ticket": value, "embedded": embedded},
    )


async def test_a_connections_ticket_opens_a_private_app_framed_until_it_ends(
    browser, hosted_app, authenticated_client, db_manager
):
    user_id = await _person_id(authenticated_client)
    grant_id = await _connection(db_manager, user_id=user_id, pod_id=hosted_app.pod_id)
    value = await _embedded_ticket(
        db_manager, user_id=user_id, hosted_app=hosted_app, grant_id=grant_id
    )

    redeemed = await _redeem(browser, hosted_app, value, embedded=True)
    assert redeemed.status_code == 200, redeemed.text
    cookie = redeemed.headers["set-cookie"]
    # Sent from a frame in another site's page, and kept to that page.
    assert all(
        flag in cookie
        for flag in ("HttpOnly", "Secure", "SameSite=none", "Partitioned", "Path=/")
    )
    claims = verify_app_access_token(
        redeemed.cookies[ACCESS_COOKIE],
        purpose=AppAccessPurpose.COOKIE,
        origin=hosted_app.origin,
    )
    assert claims is not None and claims.grant_id == grant_id

    opened = await browser.get(hosted_app.origin + "/")
    assert opened.status_code == 200, opened.text
    assert CONTENT in opened.text

    async with db_manager.session_factory() as session:
        await McpAccessRepository(SqlAlchemyUnitOfWork(session)).revoke_grant(
            grant_id=grant_id, now=datetime.now(timezone.utc)
        )
        await session.commit()
    assert_gate(await browser.get(hosted_app.origin + "/"))


async def test_neither_ticket_stands_in_for_the_other(
    browser, hosted_app, authenticated_client, db_manager
):
    """A connection's ticket opens only a framed app, and a person's own ticket
    never earns the cookie made for a frame."""
    user_id = await _person_id(authenticated_client)
    grant_id = await _connection(db_manager, user_id=user_id, pod_id=hosted_app.pod_id)
    connections = await _embedded_ticket(
        db_manager, user_id=user_id, hosted_app=hosted_app, grant_id=grant_id
    )
    assert (
        await _redeem(browser, hosted_app, connections, embedded=False)
    ).status_code == 401

    own = await ticket(authenticated_client, hosted_app.origin)
    assert own.status_code == 200, own.text
    refused = await _redeem(browser, hosted_app, own.json()["ticket"], embedded=True)
    assert refused.status_code == 401


async def test_no_ticket_for_someone_the_app_is_not_shared_with(
    hosted_app, async_client, db_manager
):
    stranger = await signup_user(async_client, "stranger")
    session = await mint_pod_agent_session(
        user_id=UUID(stranger["id"]),
        pod_id=UUID(hosted_app.pod_id),
        session_id="mcp:00000000-0000-0000-0000-000000000000",
    )
    with pytest.raises(AppNotFoundError):
        await mint_embedded_app_ticket(
            SessionUnitOfWorkFactory(db_manager.session_factory),
            slug=hosted_app.name,
            user_id=UUID(stranger["id"]),
            session_handle=session.session_handle,
            grant_id=UUID(int=1),
        )
