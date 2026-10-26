"""The tool bridge serves an Agent Host run the toolset its dispatch decided on.

Whether the run's own shell replaces Lemma's command tools is decided once, when
the run's context is built, and written on the run. The bridge every relayed
tool call goes through used to ask again, so toggling host execution mid-run
changed the tools under a prompt that said otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.agent_host.mcp import ConversationMCPService

pytestmark = [pytest.mark.e2e]


@pytest.mark.parametrize("decided", [True, False])
async def test_the_bridge_reads_the_runs_decision_back(db_session, scenario, decided):
    await scenario.create_org_with_pod(name_prefix="Native commands")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "native"}
    )
    assert created.status_code in {200, 201}, created.text
    # This deployment has no Mac with host execution, so asking afresh can only
    # answer False: a True here can only have been read back.
    run = AgentRunModel(
        conversation_id=created.json()["id"],
        status="RUNNING",
        agent_runtime={"profile_id": "system:lemma"},
        run_metadata={"host_native_commands": {"enabled": decided, "host_id": None}},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()

    (
        _agent,
        _conversation,
        ctx,
        _grants,
    ) = await ConversationMCPService()._load_agent_context(
        conversation_id=run.conversation_id, agent_run_id=run.id
    )

    assert ctx.host_runs_native_commands is decided
