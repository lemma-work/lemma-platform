"""A run a private note started delivers nothing to the platform, on Agent Host too.

The in-process runner marks such a run's context as not delivering to the
surface. The bridge every remote harness reaches its tools through builds its
own context, and has to say the same -- or a coding agent's ``display_resource``
posts the note's answer to the group it was kept from.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.agent_host.mcp import ConversationMCPService

pytestmark = [pytest.mark.e2e]


@pytest.mark.parametrize(("private_note", "delivers"), [(True, False), (False, True)])
async def test_the_tool_bridge_knows_a_notes_answer_stays_in_lemma(
    db_session, scenario, private_note, delivers
):
    await scenario.create_org_with_pod(name_prefix="Note bridge")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "note"}
    )
    assert created.status_code in {200, 201}, created.text
    run = AgentRunModel(
        conversation_id=created.json()["id"],
        status="RUNNING",
        agent_runtime={"profile_id": "system:lemma"},
        private_note=private_note,
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

    assert ctx.delivers_to_surface is delivers
