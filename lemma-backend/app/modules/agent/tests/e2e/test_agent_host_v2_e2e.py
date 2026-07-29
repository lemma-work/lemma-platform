from __future__ import annotations

import base64
import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.crypto import get_secret_cipher
from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunSpec,
    AgentHostRunState,
    HostHello,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostProtocolViolation,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostMcpRouteModel,
    AgentHostRunLeaseModel,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_host_auth import (
    host_signature_payload,
    pairing_signature_payload,
)


pytestmark = pytest.mark.e2e


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _hello(*, installation_id: str, instance_id: str | None = None) -> dict:
    return {
        "protocol_min": 2,
        "protocol_max": 2,
        "host_release": "0.1.0-test",
        "adapter_manifest_id": "sha256:test-manifest",
        "installation_id": installation_id,
        "instance_id": instance_id or str(uuid4()),
    }


async def _database_shape(database_url: str) -> tuple[set[str], set[str]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: (
                    set(inspect(sync_connection).get_table_names()),
                    {
                        column["name"]
                        for column in inspect(sync_connection).get_columns(
                            "agent_runtime_profiles"
                        )
                    },
                )
            )
    finally:
        await engine.dispose()


def test_agent_host_migration_round_trip(
    e2e_settings,
    test_database_url: str,
) -> None:
    del e2e_settings
    from app.core.config import settings

    settings.database_url = test_database_url
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    tables, profile_columns = asyncio.run(_database_shape(test_database_url))
    assert {
        "agent_hosts",
        "agent_host_integrations",
        "agent_host_commands",
        "agent_host_run_leases",
        "agent_host_events",
        "agent_host_mcp_routes",
    } <= tables
    assert "host_integration_id" in profile_columns

    command.downgrade(config, "0008_function_execution")
    tables, profile_columns = asyncio.run(_database_shape(test_database_url))
    assert "agent_hosts" not in tables
    assert "agent_host_mcp_routes" not in tables
    assert "host_integration_id" not in profile_columns

    command.upgrade(config, "head")
    tables, profile_columns = asyncio.run(_database_shape(test_database_url))
    assert "agent_host_run_leases" in tables
    assert "host_integration_id" in profile_columns


async def _pair_host(
    *,
    authenticated_client,
    org_id: str,
) -> tuple[UUID, str, dict]:
    private_key = Ed25519PrivateKey.generate()
    public_key = _b64(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    installation_id = f"test-installation-{uuid4()}"
    hello = _hello(installation_id=installation_id)
    pairing = await authenticated_client.post(
        "/me/agent-hosts/pairings",
        json={
            "display_name": "Agent Host E2E",
            "organization_id": org_id,
        },
    )
    assert pairing.status_code == 201, pairing.text
    pairing_code = pairing.json()["pairing_code"]
    pairing_nonce = f"pairing-nonce-{uuid4()}"
    pairing_timestamp = int(time.time())
    pairing_signature = _b64(
        private_key.sign(
            pairing_signature_payload(
                pairing_code=pairing_code,
                installation_id=installation_id,
                nonce=pairing_nonce,
                timestamp=pairing_timestamp,
            )
        )
    )
    completed = await authenticated_client.post(
        "/agent-host/v2/pairings:complete",
        json={
            "pairing_code": pairing_code,
            "public_key": public_key,
            "display_name": "Agent Host E2E",
            "hello": hello,
            "nonce": pairing_nonce,
            "timestamp": pairing_timestamp,
            "signature": pairing_signature,
        },
    )
    assert completed.status_code == 200, completed.text
    host_id = UUID(completed.json()["host_id"])
    nonce = f"nonce-{uuid4()}"
    timestamp = int(time.time())
    signature = _b64(
        private_key.sign(
            host_signature_payload(
                host_id=host_id,
                nonce=nonce,
                timestamp=timestamp,
            )
        )
    )
    token_response = await authenticated_client.post(
        "/agent-host/v2/token:exchange",
        json={
            "host_id": str(host_id),
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        },
    )
    assert token_response.status_code == 200, token_response.text
    return host_id, token_response.json()["access_token"], hello


async def _create_run(
    *,
    authenticated_client,
    fixed_test_org: dict,
    db_session,
    profile_id: str,
) -> tuple[UUID, UUID]:
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Agent Host E2E Pod {uuid4().hex[:8]}",
            "description": "Agent Host protocol persistence test",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert pod_response.status_code == 201, pod_response.text
    pod_id = pod_response.json()["id"]
    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Agent Host E2E Agent",
            "instruction": "Return the test response.",
            "agent_runtime": {"profile_id": profile_id},
        },
    )
    assert agent_response.status_code == 201, agent_response.text
    conversation_response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_response.json()["name"],
            "title": "Agent Host durable protocol",
            "type": "CHAT",
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = UUID(conversation_response.json()["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    run = await ConversationRepository(uow).create_agent_run(
        conversation_id=conversation_id,
        agent_id=UUID(agent_response.json()["id"]),
        agent_runtime=AgentRuntimeConfig(profile_id=profile_id),
        metadata={"source": "agent_host_v2_e2e"},
    )
    await uow.commit()
    return run.id, conversation_id


async def _enqueue_timeout_run(
    *,
    authenticated_client,
    fixed_test_org,
    db_session,
    profile_id: str,
    host_id: UUID,
    integration_id: UUID,
    now: datetime,
) -> tuple[AgentHostDispatchRepository, UUID]:
    run_id, conversation_id = await _create_run(
        authenticated_client=authenticated_client,
        fixed_test_org=fixed_test_org,
        db_session=db_session,
        profile_id=profile_id,
    )
    encrypted_mcp = await get_secret_cipher().encrypt_json_async(
        {
            "url": "https://lemma.test/mcp",
            "authorization": "Bearer timeout-test",
        }
    )
    assert encrypted_mcp is not None
    dispatch = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    await dispatch.enqueue_run(
        host_id=host_id,
        integration_id=integration_id,
        runtime_profile_id=UUID(profile_id),
        run_spec=AgentHostRunSpec(
            agent_run_id=run_id,
            conversation_id=conversation_id,
            integration_id=integration_id,
            profile_revision="codex-config-revision-2",
            config_selections={"model": "gpt-test"},
            system_prompt="Timeout test",
            prompt=[{"type": "text", "text": "Do not duplicate"}],
            context={},
            mcp_route_id=str(uuid4()),
            run_deadline=now + timedelta(minutes=10),
        ),
        encrypted_mcp_payload=encrypted_mcp,
        now=now,
        command_ttl_seconds=1,
    )
    await db_session.commit()
    return dispatch, run_id


async def test_agent_host_v2_is_durable_fenced_and_idempotent(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
) -> None:
    host_id, device_token, hello = await _pair_host(
        authenticated_client=authenticated_client,
        org_id=fixed_test_org["id"],
    )
    device_headers = {"Authorization": f"Bearer {device_token}"}
    now = datetime.now(timezone.utc)
    repository_uow = SqlAlchemyUnitOfWork(db_session)
    await AgentHostRepository(repository_uow).mark_seen(
        host_id=host_id,
        hello=HostHello.model_validate(hello),
        capacity={"max_runs": 2, "active_runs": 0, "available_runs": 2},
        now=now,
    )
    await repository_uow.commit()
    publish = await authenticated_client.put(
        "/agent-host/v2/integrations",
        headers=device_headers,
        json={
            "integrations": [
                {
                    "integration_key": "codex",
                    "display_name": "Codex",
                    "adapter_protocol": "ACP_V1",
                    "adapter_version": "1.0.0-test",
                    "upstream_version": "0.144.5",
                    "auth_state": "READY",
                    "health": "READY",
                    "capabilities": {
                        "load_session": True,
                        "usage": True,
                    },
                    "config_revision": "codex-config-revision-1",
                    "config_options": [
                        {
                            "id": "model",
                            "category": "model",
                            "name": "Model",
                            "current_value": "gpt-test",
                            "options": [
                                {"value": "gpt-test", "name": "GPT Test"},
                                {"value": "gpt-new", "name": "GPT New"},
                            ],
                        }
                    ],
                    "fetched_at": now.isoformat(),
                    "stale_after": (now + timedelta(hours=1)).isoformat(),
                }
            ]
        },
    )
    assert publish.status_code == 200, publish.text
    integration_id = UUID(publish.json()["items"][0]["id"])

    profile_response = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "AGENT_HOST",
            "host_integration_id": str(integration_id),
            "scope": "PERSONAL",
            "name": "Codex through Agent Host",
            "integration_snapshot_revision": "codex-config-revision-1",
            "config_selections": {"model": "gpt-test"},
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["id"]

    # Provider catalogs are live, not copied into profiles. Publishing a new
    # model revision keeps the existing selection valid and makes the new model
    # immediately available in the generic runtime picker.
    refreshed_at = now + timedelta(seconds=1)
    refresh = await authenticated_client.put(
        "/agent-host/v2/integrations",
        headers=device_headers,
        json={
            "integrations": [
                {
                    "integration_key": "codex",
                    "display_name": "Codex",
                    "adapter_protocol": "ACP_V1",
                    "adapter_version": "1.0.0-test",
                    "upstream_version": "0.144.5",
                    "auth_state": "READY",
                    "health": "READY",
                    "capabilities": {
                        "load_session": True,
                        "usage": True,
                    },
                    "config_revision": "codex-config-revision-2",
                    "config_options": [
                        {
                            "id": "model",
                            "category": "model",
                            "name": "Model",
                            "current_value": "gpt-test",
                            "options": [
                                {"value": "gpt-test", "name": "GPT Test"},
                                {"value": "gpt-new", "name": "GPT New"},
                                {"value": "gpt-future", "name": "GPT Future"},
                            ],
                        }
                    ],
                    "fetched_at": refreshed_at.isoformat(),
                    "stale_after": (refreshed_at + timedelta(hours=1)).isoformat(),
                }
            ]
        },
    )
    assert refresh.status_code == 200, refresh.text
    profiles = await authenticated_client.get(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles"
    )
    assert profiles.status_code == 200, profiles.text
    live_profile = next(
        item for item in profiles.json()["items"] if item["id"] == profile_id
    )
    assert live_profile["availability_status"] == "READY"
    assert live_profile["integration_config_revision"] == "codex-config-revision-2"
    assert {item["name"] for item in live_profile["model_catalog"]} >= {
        "gpt-test",
        "gpt-future",
    }

    run_id, conversation_id = await _create_run(
        authenticated_client=authenticated_client,
        fixed_test_org=fixed_test_org,
        db_session=db_session,
        profile_id=profile_id,
    )
    route_id = uuid4()
    mcp_secret = {
        "server_name": "lemma_tools",
        "url": "https://lemma.test/mcp",
        "authorization": "Bearer secret-workspace-capability",
    }
    encrypted_mcp = await get_secret_cipher().encrypt_json_async(mcp_secret)
    assert encrypted_mcp is not None
    dispatch = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    await dispatch.enqueue_run(
        host_id=host_id,
        integration_id=integration_id,
        runtime_profile_id=UUID(profile_id),
        run_spec=AgentHostRunSpec(
            agent_run_id=run_id,
            conversation_id=conversation_id,
            integration_id=integration_id,
            profile_revision="codex-config-revision-2",
            config_selections={"model": "gpt-test"},
            system_prompt="System test prompt",
            prompt=[{"type": "text", "text": "Say hello"}],
            context={},
            mcp_route_id=str(route_id),
            run_deadline=now + timedelta(minutes=10),
        ),
        encrypted_mcp_payload=encrypted_mcp,
    )
    await db_session.commit()

    # A draining/full host must not receive another start command. The command
    # stays queued and is delivered once capacity is advertised again.
    blocked = await dispatch.poll_commands(
        host_id=host_id,
        limit=16,
        acknowledged_command_ids=[],
        checkpoints=[],
        available_run_slots=0,
    )
    assert blocked == []
    await db_session.commit()

    poll = await authenticated_client.post(
        "/agent-host/v2/poll",
        headers=device_headers,
        json={
            "hello": hello,
            "capacity": {
                "max_runs": 2,
                "active_runs": 0,
                "available_runs": 2,
            },
        },
    )
    assert poll.status_code == 200, poll.text
    commands = poll.json()["commands"]
    assert len(commands) == 1
    command = commands[0]
    assert command["kind"] == "START_RUN"
    assert command["payload"]["mcp_route_id"] == str(route_id)
    assert "authorization" not in str(command["payload"]).lower()
    assert "secret-workspace-capability" not in str(command["payload"])
    stored_route = (
        await db_session.execute(
            select(AgentHostMcpRouteModel).where(AgentHostMcpRouteModel.id == route_id)
        )
    ).scalar_one()
    assert "secret-workspace-capability" not in str(stored_route.encrypted_payload)

    route = await authenticated_client.get(
        f"/agent-host/v2/mcp-routes/{route_id}",
        headers=device_headers,
    )
    assert route.status_code == 200, route.text
    assert route.json()["mcp"] == mcp_secret

    ack = await authenticated_client.post(
        "/agent-host/v2/poll",
        headers=device_headers,
        json={
            "hello": hello,
            "capacity": {
                "max_runs": 2,
                "active_runs": 1,
                "available_runs": 1,
            },
            "acknowledged_command_ids": [command["command_id"]],
            "checkpoints": [
                {
                    "run_id": str(run_id),
                    "lease_epoch": 1,
                    "checkpoint": "ACCEPTED",
                    "state": "ACCEPTED",
                }
            ],
        },
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["commands"] == []

    lease = await db_session.get(AgentHostRunLeaseModel, run_id)
    assert lease is not None
    lease.lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()
    expired_route = await authenticated_client.get(
        f"/agent-host/v2/mcp-routes/{route_id}",
        headers=device_headers,
    )
    assert expired_route.status_code == 409, expired_route.text

    recovering = await dispatch.reconcile_expired_run(
        run_id=run_id,
        now=now,
    )
    assert recovering is not None
    assert recovering.state == "RECOVERING"
    await db_session.commit()
    recovered = await authenticated_client.post(
        "/agent-host/v2/poll",
        headers=device_headers,
        json={
            "hello": hello,
            "capacity": {
                "max_runs": 2,
                "active_runs": 1,
                "available_runs": 1,
            },
            "checkpoints": [
                {
                    "run_id": str(run_id),
                    "lease_epoch": 1,
                    "checkpoint": "RUNNING",
                    "state": "RUNNING",
                }
            ],
        },
    )
    assert recovered.status_code == 200, recovered.text
    await db_session.refresh(lease)
    assert lease.state == "RUNNING"

    first_event = AgentHostEvent(
        run_id=run_id,
        lease_epoch=1,
        sequence=1,
        event_id=uuid4(),
        occurred_at=now,
        type=AgentHostEventType.AGENT_MESSAGE_UPSERT,
        object_id="message-1",
        payload={"text": "hello from Codex"},
        integration_key="codex",
        adapter_version="1.0.0-test",
    )
    batch = AgentHostEventBatch(events=[first_event]).model_dump(mode="json")
    first_append = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=batch,
    )
    assert first_append.status_code == 200, first_append.text
    assert first_append.json()["acked_through"] == 1

    duplicate = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=batch,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["acked_through"] == 1

    gap_event = first_event.model_copy(update={"sequence": 3, "event_id": uuid4()})
    gap = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=AgentHostEventBatch(events=[gap_event]).model_dump(mode="json"),
    )
    assert gap.status_code == 409, gap.text
    assert "event sequence gap" in gap.text

    mixed_run_event = first_event.model_copy(
        update={
            "run_id": uuid4(),
            "sequence": 2,
            "event_id": uuid4(),
        }
    )
    with pytest.raises(AgentHostProtocolViolation, match="multiple run leases"):
        await dispatch.append_events(
            host_id=host_id,
            batch=AgentHostEventBatch.model_construct(
                events=[
                    first_event.model_copy(update={"sequence": 2, "event_id": uuid4()}),
                    mixed_run_event,
                ]
            ),
        )
    await db_session.rollback()

    terminal_event = first_event.model_copy(
        update={
            "sequence": 2,
            "event_id": uuid4(),
            "type": AgentHostEventType.TERMINAL,
            "object_id": None,
            "payload": {"state": "SUCCEEDED"},
        }
    )
    terminal_append = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=AgentHostEventBatch(events=[terminal_event]).model_dump(mode="json"),
    )
    assert terminal_append.status_code == 200, terminal_append.text
    assert terminal_append.json()["acked_through"] == 2

    terminal_checkpoint = await authenticated_client.post(
        "/agent-host/v2/poll",
        headers=device_headers,
        json={
            "hello": hello,
            "capacity": {
                "max_runs": 2,
                "active_runs": 0,
                "available_runs": 2,
            },
            "checkpoints": [
                {
                    "run_id": str(run_id),
                    "lease_epoch": 1,
                    "checkpoint": "TERMINAL",
                    "state": "SUCCEEDED",
                }
            ],
        },
    )
    assert terminal_checkpoint.status_code == 200, terminal_checkpoint.text

    terminal_replay = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=AgentHostEventBatch(events=[terminal_event]).model_dump(mode="json"),
    )
    assert terminal_replay.status_code == 200, terminal_replay.text
    assert terminal_replay.json()["acked_through"] == 2

    after_terminal = first_event.model_copy(update={"sequence": 3, "event_id": uuid4()})
    after_terminal_append = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=AgentHostEventBatch(events=[after_terminal]).model_dump(mode="json"),
    )
    assert after_terminal_append.status_code == 409, after_terminal_append.text
    assert "terminal run cannot accept events" in after_terminal_append.text

    stale_event = first_event.model_copy(
        update={
            "lease_epoch": 2,
            "sequence": 3,
            "event_id": uuid4(),
        }
    )
    stale = await authenticated_client.post(
        "/agent-host/v2/events:append",
        headers=device_headers,
        json=AgentHostEventBatch(events=[stale_event]).model_dump(mode="json"),
    )
    assert stale.status_code == 409, stale.text
    assert "stale run lease epoch" in stale.text

    fenced_route = await authenticated_client.get(
        f"/agent-host/v2/mcp-routes/{route_id}",
        headers=device_headers,
    )
    assert fenced_route.status_code == 409, fenced_route.text

    rows = await AgentHostDispatchRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).events_after(run_id=run_id, sequence=0)
    assert [(row.sequence, row.type) for row in rows] == [
        (1, "agent_message_upsert"),
        (2, "terminal"),
    ]
    assert fixed_test_user["id"] == profile_response.json()["user_id"]

    queued_dispatch, queued_run_id = await _enqueue_timeout_run(
        authenticated_client=authenticated_client,
        fixed_test_org=fixed_test_org,
        db_session=db_session,
        profile_id=profile_id,
        host_id=host_id,
        integration_id=integration_id,
        now=now,
    )
    queued_timeout = await queued_dispatch.expire_unaccepted_run(
        run_id=queued_run_id,
        now=now + timedelta(seconds=2),
    )
    assert queued_timeout is AgentHostRunState.FAILED
    await db_session.commit()

    delivered_dispatch, delivered_run_id = await _enqueue_timeout_run(
        authenticated_client=authenticated_client,
        fixed_test_org=fixed_test_org,
        db_session=db_session,
        profile_id=profile_id,
        host_id=host_id,
        integration_id=integration_id,
        now=now,
    )
    delivered_commands = await delivered_dispatch.poll_commands(
        host_id=host_id,
        limit=16,
        acknowledged_command_ids=[],
        checkpoints=[],
        available_run_slots=1,
        now=now,
        lease_seconds=1,
    )
    assert len(delivered_commands) == 1
    await db_session.commit()
    delivered_timeout = await delivered_dispatch.expire_unaccepted_run(
        run_id=delivered_run_id,
        now=now + timedelta(seconds=2),
    )
    assert delivered_timeout is AgentHostRunState.DISPATCH_UNKNOWN
    await db_session.commit()

    revoked = await authenticated_client.post(
        "/agent-host/v2/revoke",
        headers=device_headers,
    )
    assert revoked.status_code == 204, revoked.text
    rejected_after_revoke = await authenticated_client.post(
        "/agent-host/v2/poll",
        headers=device_headers,
        json={
            "hello": hello,
            "capacity": {
                "max_runs": 2,
                "active_runs": 0,
                "available_runs": 2,
            },
        },
    )
    assert rejected_after_revoke.status_code == 401
