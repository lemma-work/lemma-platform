"""A deleted account takes its connected clients with it.

The verifier and the refresh path already refuse a deleted account's tokens
(``account_may_sign_in``); this ends the grants too, so their tokens leave the
table now rather than waiting out the 180-day ceiling, and nothing the person
consented to is left standing.

A FAST work item written by the deletion: main-database statements only,
committed with the item's removal.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.identity.contracts.account_facts import AccountDeleted
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository

MCP_ACCESS_ACCOUNT_DELETED = "mcp_access.account_deleted"


async def end_deleted_account_grants(ctx: FastContext, payload: AccountDeleted) -> None:
    await McpAccessRepository(ctx.uow).revoke_user_grants(
        user_id=payload.user_id, now=datetime.now(timezone.utc)
    )


def register_mcp_access_account_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=MCP_ACCESS_ACCOUNT_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AccountDeleted,
    )(end_deleted_account_grants)


register_mcp_access_account_work(default_registry)
facts.subscribe(
    AccountDeleted,
    kind=MCP_ACCESS_ACCOUNT_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"mcp-access-account-deleted:{fact.user_id}",
)
