"""The HTTP surface a paired computer actually speaks, over real HTTP.

Every other Agent Host test drives repositories and services directly, so no
test had ever issued a request to `/agent-host/*`. Three bugs shipped behind
that gap, each hiding the next:

* the global `verify_auth` dependency has an allowlist and `/agent-host` was not
  on it, so every one of these routes 401'd - a paired computer has no user
  session and never will;
* `pairings:complete` is the one route whose credential *is* its body, and
  nothing checked that it works without a session;
* the idle poll called `asyncio.wait_for(anext(...))`, whose timeout cancels and
  closes the async generator, so the second idle round raised
  StopAsyncIteration and 500'd - every host went OFFLINE five seconds after
  connecting.

These use `async_client` (no session) deliberately. Reaching for
`authenticated_client` here would re-hide exactly what needs proving.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.agent.api.controllers import agent_host_controller

pytestmark = pytest.mark.e2e

def _hello(installation_id: str | None = None) -> dict:
    """One machine = one installation_id. Re-pairing the same installation
    supersedes its previous credential, so tests that want two live machines
    must use two ids."""
    return {
        "installation_id": installation_id or f"e2e-{uuid4()}",
        "host_release": "0.1.0",
        "protocol_version": agent_host_controller.AGENT_HOST_PROTOCOL_VERSION,
    }


def _stale_after() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


async def _pair(
    authenticated_client, async_client, *, display_name: str, hello: dict | None = None
) -> dict:
    """Mint a code as the user, then consume it as the machine would."""
    hello = hello or _hello()
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
            "hello": hello,
        },
    )
    assert completed.status_code == status.HTTP_200_OK, completed.text
    return {**completed.json(), "hello": hello}


@pytest.mark.asyncio
async def test_a_machine_pairs_without_a_user_session(
    authenticated_client, async_client
):
    """The pairing code is the credential. Requiring a session here 401s the
    only caller this route has."""
    paired = await _pair(authenticated_client, async_client, display_name="e2e laptop")

    assert paired["host_secret"]
    assert paired["host_id"]

    listed = await authenticated_client.get("/me/runtime/agent-hosts")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert paired["host_id"] in {item["id"] for item in listed.json()["items"]}
    # The secret is issued exactly once and never read back.
    assert paired["host_secret"] not in listed.text


@pytest.mark.asyncio
async def test_a_pairing_code_is_single_use(authenticated_client, async_client):
    minted = await authenticated_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "e2e once", "organization_id": None},
    )
    body = {
        "pairing_code": minted.json()["pairing_code"],
        "display_name": "e2e once",
        "hello": _hello(),
    }

    first = await async_client.post("/agent-host/pairings:complete", json=body)
    assert first.status_code == status.HTTP_200_OK, first.text

    replayed = await async_client.post("/agent-host/pairings:complete", json=body)
    assert replayed.status_code != status.HTTP_200_OK
    assert first.json()["host_secret"] not in replayed.text


@pytest.mark.asyncio
async def test_the_host_polls_with_its_secret_and_survives_an_idle_round(
    authenticated_client, async_client, monkeypatch
):
    """A poll that finds no work must stay a 200 across several idle rounds.

    The idle wait is shortened so this exercises more than one round in about a
    second; at the shipped 5s/25s it would take half a minute to reach the
    round that used to fail.
    """
    monkeypatch.setattr(agent_host_controller, "_IDLE_REPOLL_SECONDS", 0.2)
    monkeypatch.setattr(agent_host_controller, "_LONG_POLL_SECONDS", 1.0)

    paired = await _pair(authenticated_client, async_client, display_name="e2e poller")
    headers = {"Authorization": f"Bearer {paired['host_secret']}"}
    poll_body = {
        "hello": paired["hello"],
        "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
    }

    polled = await async_client.post(
        "/agent-host/poll", json=poll_body, headers=headers
    )
    assert polled.status_code == status.HTTP_200_OK, polled.text
    assert polled.json()["commands"] == []

    # Polling again is what the host does forever; it must not degrade.
    again = await async_client.post("/agent-host/poll", json=poll_body, headers=headers)
    assert again.status_code == status.HTTP_200_OK, again.text


@pytest.mark.asyncio
async def test_polling_refuses_an_unknown_or_revoked_secret(
    authenticated_client, async_client
):
    paired = await _pair(authenticated_client, async_client, display_name="e2e revoked")
    poll_body = {
        "hello": paired["hello"],
        "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
    }

    unknown = await async_client.post(
        "/agent-host/poll",
        json=poll_body,
        headers={"Authorization": "Bearer not-a-real-host-secret"},
    )
    assert unknown.status_code == status.HTTP_401_UNAUTHORIZED

    missing = await async_client.post("/agent-host/poll", json=poll_body)
    assert missing.status_code == status.HTTP_401_UNAUTHORIZED

    revoked = await authenticated_client.delete(
        f"/me/runtime/agent-hosts/{paired['host_id']}"
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text

    after = await async_client.post(
        "/agent-host/poll",
        json=poll_body,
        headers={"Authorization": f"Bearer {paired['host_secret']}"},
    )
    assert after.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_a_paired_host_publishes_the_harnesses_the_workspace_can_use(
    authenticated_client, async_client
):
    """Publishing is what turns a paired machine into pickable chat models."""
    paired = await _pair(authenticated_client, async_client, display_name="e2e agents")
    headers = {"Authorization": f"Bearer {paired['host_secret']}"}

    published = await async_client.put(
        "/agent-host/harnesses",
        json={
            "harnesses": [
                {
                    "harness_key": "opencode",
                    "display_name": "OpenCode",
                    "adapter_version": "1.0.0",
                    "upstream_version": "0.1.0",
                    "health": "READY",
                    "config_revision": "rev-1",
                    "config_options": [],
                    "stale_after": _stale_after(),
                }
            ]
        },
        headers=headers,
    )
    assert published.status_code == status.HTTP_200_OK, published.text

    harnesses = await authenticated_client.get(
        f"/me/runtime/agent-hosts/{paired['host_id']}/harnesses"
    )
    assert harnesses.status_code == status.HTTP_200_OK, harnesses.text
    assert "opencode" in {item["harness_key"] for item in harnesses.json()["items"]}


@pytest.mark.asyncio
async def test_concurrent_idle_polls_all_return(authenticated_client, async_client):
    """Two machines idling at once is the normal state of a workspace."""
    first = await _pair(authenticated_client, async_client, display_name="e2e a")
    second = await _pair(authenticated_client, async_client, display_name="e2e b")
    responses = await asyncio.gather(
        *(
            async_client.post(
                "/agent-host/poll",
                json={
                    "hello": host["hello"],
                    "capacity": {
                        "max_runs": 1,
                        "active_runs": 0,
                        "available_runs": 1,
                    },
                },
                headers={"Authorization": f"Bearer {host['host_secret']}"},
            )
            for host in (first, second)
        )
    )

    assert [response.status_code for response in responses] == [
        status.HTTP_200_OK,
        status.HTTP_200_OK,
    ]
