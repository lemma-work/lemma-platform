"""An Agent Host run's tool calls derive its runtime and grants once a lease.

Each relayed tool call used to resolve the run's runtime profile and read the
agent's grants again. They are kept in Redis for the run's lease now, and a
change to the agent's grants throws them away. Against real Postgres and Redis,
because the point is what is *not* read again, and what makes it be read.
"""

from __future__ import annotations

import pytest

from app.core.authorization.grants import replace_grantee_resource_grants
from app.core.authorization.service import AuthorizationDataService
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.agent_host import relayed_session as mcp_module
from app.modules.agent.agent_host.mcp import (
    ConversationMCPService,
)
from app.modules.agent.agent_host.relayed_session import (
    MCP_INPUTS_TTL_SECONDS,
    RunInputsCache,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    running_run,
    seeded_conversation,
)

pytestmark = [pytest.mark.e2e]


class _Counted:
    def __init__(self) -> None:
        self.runtime_resolves = 0
        self.grant_reads = 0


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> _Counted:
    counts = _Counted()
    resolve = mcp_module.resolved_runtime_profile
    read_grants = mcp_module.load_agent_grant_summary

    async def _resolve(**kwargs):
        counts.runtime_resolves += 1
        return await resolve(**kwargs)

    async def _read_grants(uow_factory, *, agent):
        counts.grant_reads += 1
        return await read_grants(uow_factory, agent=agent)

    monkeypatch.setattr(mcp_module, "resolved_runtime_profile", _resolve)
    monkeypatch.setattr(mcp_module, "load_agent_grant_summary", _read_grants)
    return counts


def _service() -> ConversationMCPService:
    return ConversationMCPService(
        inputs_cache=RunInputsCache(
            RedisJsonCache(
                redis_url=settings.redis_url,
                key_prefix="agent:mcp-inputs",
                ttl_seconds=MCP_INPUTS_TTL_SECONDS,
            )
        )
    )


async def _list(service: ConversationMCPService, seeded: Seeded, run_id) -> set[str]:
    tools = await service.list_tools(
        conversation_id=seeded.conversation_id, agent_run_id=run_id
    )
    return {tool.name for tool in tools}


async def test_calls_in_one_lease_derive_once_and_a_grant_change_derives_again(
    scenario, counted: _Counted
) -> None:
    seeded = await seeded_conversation(scenario, toolsets=["TODO"])
    run_id = await running_run(seeded)
    service = _service()

    first = await _list(service, seeded, run_id)
    second = await _list(service, seeded, run_id)

    assert first == second and first
    assert (counted.runtime_resolves, counted.grant_reads) == (1, 1)

    # Any write to the agent's grants -- here, replacing them with none.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        owner = await AuthorizationDataService(uow.session).build_user_context(
            user_id=seeded.user_id, pod_id=seeded.pod_id
        )
        await replace_grantee_resource_grants(
            uow.session,
            ctx=owner,
            pod_id=seeded.pod_id,
            grantee_type="AGENT",
            grantee_id=seeded.agent_id,
            grants=[],
            created_by_user_id=seeded.user_id,
        )
        await uow.commit()

    await _list(service, seeded, run_id)
    assert (counted.runtime_resolves, counted.grant_reads) == (2, 2)
