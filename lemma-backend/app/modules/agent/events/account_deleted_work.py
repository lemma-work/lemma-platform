"""A deleted account's computers stop working for it.

Subscribed to identity's ``AccountDeleted``. A paired Agent Host authenticates
with its own secret, and that check reads the host's ``revoked_at`` -- not its
owner's standing -- so a computer paired before the deletion would otherwise go
on connecting, and taking runs, for an account that no longer exists. Each of
the person's hosts is revoked the way removing it in settings does: its
unfinished runs fail and its pending commands are cancelled in the same
transaction, and any link already open is told to close once that commits.

A JOB, not FAST: the notice is a publish to Redis, after the commit.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.infrastructure.agent_host.channels import notify_host_revoked
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.identity.contracts.account_facts import AccountDeleted

AGENT_HOSTS_ACCOUNT_DELETED = "agent.hosts.account_deleted"


async def revoke_deleted_account_hosts(
    ctx: JobContext, payload: AccountDeleted
) -> None:
    revoked: list[UUID] = []
    async with ctx.complete_work() as uow:
        repository = AgentHostRepository(uow)
        for host in await repository.list_for_user(user_id=payload.user_id):
            if host.revoked_at is None:
                await repository.revoke(host_id=host.id, user_id=payload.user_id)
                revoked.append(host.id)
    for host_id in revoked:
        await notify_host_revoked(host_id)


def register_account_deleted_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_HOSTS_ACCOUNT_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=AccountDeleted,
    )(revoke_deleted_account_hosts)


register_account_deleted_work(default_registry)
facts.subscribe(
    AccountDeleted,
    kind=AGENT_HOSTS_ACCOUNT_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"agent-hosts-account-deleted:{fact.user_id}",
)
