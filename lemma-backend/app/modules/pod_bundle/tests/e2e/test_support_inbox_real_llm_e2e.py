"""Gated real-LLM e2e: an imported agent runs on a real model and uses the
imported function end to end.

Imports the ``support_inbox`` bundle, points ``support_agent`` at the real
``system:lemma`` runtime, and gives it a customer email. With a real model the
agent must call the imported ``triage_ticket`` tool (which it can only do because
the import applied its POD toolset + ``function.execute`` grant), and that
function writes a ticket to the imported table (its own record grants). We assert
the ticket landed — the whole imported pod working under a real model.

Gated: needs real provider creds (``system_lemma_available``) + the Docker
the sandbox runtime (run under ``E2E_REAL=1``); skipped in the mock e2e gate.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import status

from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
)
from app.modules.agent.tests.e2e.test_agent_e2e import (
    DEFAULT_AGENT_RUNTIME,
    _assert_completed_without_error,
    _post_sse,
)
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    import_and_apply,
    pack_fixture_bundle,
    provision_workspace,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.worker,
    pytest.mark.workspace,
    pytest.mark.real_llm,
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]


async def _tickets(client, pod_id: str) -> list[dict]:
    resp = await client.get(f"/pods/{pod_id}/datastore/tables/tickets/records")
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()["items"]


async def test_imported_support_agent_files_ticket_with_real_model(
    authenticated_client, test_pod, worker, workspace_api
):
    pod_id = test_pod["id"]

    await provision_workspace(authenticated_client, pod_id)
    await import_and_apply(authenticated_client, pod_id, pack_fixture_bundle("support_inbox"))

    # Point the imported agent at the real runtime (the bundle is runtime-agnostic).
    patch = await authenticated_client.patch(
        f"/pods/{pod_id}/agents/support_agent",
        json={"agent_runtime": DEFAULT_AGENT_RUNTIME},
    )
    assert patch.status_code == status.HTTP_200_OK, patch.text

    convo = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "support_agent", "title": "support e2e", "type": "CHAT"},
    )
    assert convo.status_code == status.HTTP_201_CREATED, convo.text
    conversation_id = convo.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {
            "content": (
                "A customer just emailed. Subject: 'URGENT: I was double charged'. "
                "Body: 'You billed my card twice and I need a refund today.' "
                "Please file this support ticket."
            )
        },
    )
    _assert_completed_without_error(events)

    # The agent had only the imported POD toolset + function.execute grant to work
    # with, so a filed ticket proves the imported function ran with its imported
    # grants under a real model. Poll briefly for the tool's write to settle.
    rows: list[dict] = []
    for _ in range(10):
        rows = await _tickets(authenticated_client, pod_id)
        if rows:
            break
        await asyncio.sleep(1)
    assert rows, "support_agent did not file any ticket"
    assert any(r.get("priority") == "HIGH" for r in rows), rows
