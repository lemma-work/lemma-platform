"""Shared setup for the Agent Host end-to-end suites.

A paired machine is the starting point for anything that touches dispatch, and
building one means going through the real pairing exchange: a code minted by a
signed-in user, consumed by a machine that has no session and never will. Both
suites need it, and the lease rows are keyed on the host and harness it
produces, so inventing ids instead would only exercise the foreign keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import status

from app.modules.agent.api.controllers import agent_host_controller
from app.modules.agent.domain.agent_host import AgentHostRunState
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


def hello(installation_id: str | None = None) -> dict:
    """One machine is one ``installation_id``.

    Re-pairing the same installation supersedes its previous credential, so a
    test that wants two live machines must use two ids.
    """
    return {
        "installation_id": installation_id or f"e2e-{uuid4()}",
        "host_release": "0.1.0",
        "protocol_version": agent_host_controller.AGENT_HOST_PROTOCOL_VERSION,
    }


def stale_after() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


async def pair(
    authenticated_client,
    async_client,
    *,
    display_name: str,
    machine: dict | None = None,
) -> dict:
    """Mint a code as the user, then consume it as the machine would."""
    machine = machine or hello()
    minted = await authenticated_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": display_name, "organization_id": None},
    )
    assert minted.status_code == status.HTTP_200_OK, minted.text

    completed = await async_client.post(
        "/agent-host/pairings:complete",
        json={
            "pairing_code": minted.json()["pairing_code"],
            "display_name": display_name,
            "hello": machine,
        },
    )
    assert completed.status_code == status.HTTP_200_OK, completed.text
    return {**completed.json(), "hello": machine}


async def paired_machine(
    scenario,
    *,
    display_name: str = "e2e machine",
    harness_key: str = "codex",
    load_session: bool = True,
) -> dict:
    """A paired machine with one published harness, and its credential.

    Returns the pairing response plus ``host_id`` and ``harness_id`` as UUIDs,
    so callers can build lease rows against real foreign keys.
    """
    paired = await pair(
        scenario.owner_client, scenario.async_client, display_name=display_name
    )
    published = await scenario.async_client.put(
        "/agent-host/harnesses",
        json={
            "harnesses": [
                {
                    "harness_key": harness_key,
                    "display_name": harness_key.title(),
                    "adapter_version": "1.0.0",
                    "health": "READY",
                    "capabilities": {"load_session": load_session},
                    "config_revision": "rev-1",
                    "config_options": [],
                    "stale_after": stale_after(),
                }
            ]
        },
        headers={"Authorization": f"Bearer {paired['host_secret']}"},
    )
    assert published.status_code == status.HTTP_200_OK, published.text
    return {
        **paired,
        "host_id": UUID(paired["host_id"]),
        "harness_id": UUID(published.json()["items"][0]["id"]),
    }


async def conversation_with_a_leased_run(
    db_session,
    scenario,
    *,
    host_id: UUID,
    harness_id: UUID,
    state: AgentHostRunState = AgentHostRunState.ACCEPTED,
    title: str = "e2e",
) -> tuple[UUID, UUID]:
    """A conversation mid-run, as a real dispatched turn leaves it."""
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": title},
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])

    now = datetime.now(timezone.utc)
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=now,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        AgentHostRunLeaseModel(
            run_id=run.id,
            host_id=host_id,
            harness_id=harness_id,
            lease_epoch=1,
            state=state.value,
            # Accepted is the fence past which a run is never repeated, so a
            # test standing in for a dispatched run has to have crossed it.
            accepted_at=now if state is not AgentHostRunState.QUEUED_FOR_HOST else None,
            lease_expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    return conversation_id, run.id
