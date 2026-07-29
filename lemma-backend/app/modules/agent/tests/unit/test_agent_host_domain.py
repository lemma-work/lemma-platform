"""Unit contract for Agent Host wire/domain types."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostAdapterProtocol,
    AgentHostCommand,
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostCheckpoint,
    AgentHostHarnessHealth,
    AgentHostHarnessSnapshot,
    AgentHostStatus,
    HostHello,
    canonical_json_sha256,
    checkpoint_advances,
    effective_agent_host_status,
)


NOW = datetime.now(timezone.utc)


def test_hello_negotiates_only_an_overlapping_protocol() -> None:
    hello = HostHello(
        protocol_min=AGENT_HOST_PROTOCOL_VERSION,
        protocol_max=AGENT_HOST_PROTOCOL_VERSION,
        host_release="2026.8.0",
        adapter_manifest_id="sha256:test",
        installation_id="installation",
        instance_id=uuid4(),
    )

    assert hello.negotiate() == AGENT_HOST_PROTOCOL_VERSION

    incompatible = hello.model_copy(
        update={"protocol_min": 10, "protocol_max": 12}
    )
    with pytest.raises(ValueError, match="does not include"):
        incompatible.negotiate()


def test_command_requires_run_fencing_for_run_commands() -> None:
    with pytest.raises(ValidationError, match="requires run_id and lease_epoch"):
        AgentHostCommand(
            command_id=uuid4(),
            kind=AgentHostCommandKind.START_RUN,
            created_at=NOW,
            expires_at=NOW,
            payload_sha256="0" * 64,
            payload={},
        )


def _event(sequence: int, *, run_id=None, lease_epoch: int = 1) -> AgentHostEvent:
    return AgentHostEvent(
        run_id=run_id or uuid4(),
        lease_epoch=lease_epoch,
        sequence=sequence,
        event_id=uuid4(),
        occurred_at=NOW,
        type=AgentHostEventType.AGENT_MESSAGE_CHUNK,
        object_id="message-1",
        payload={"text": "hello"},
        harness_key="codex",
        adapter_version="1.0.0",
    )


def test_event_batch_is_one_contiguous_run_epoch() -> None:
    run_id = uuid4()
    batch = AgentHostEventBatch(
        events=[_event(1, run_id=run_id), _event(2, run_id=run_id)]
    )
    assert [event.sequence for event in batch.events] == [1, 2]

    with pytest.raises(ValidationError, match="contiguous"):
        AgentHostEventBatch(
            events=[_event(1, run_id=run_id), _event(3, run_id=run_id)]
        )

    with pytest.raises(ValidationError, match="one run"):
        AgentHostEventBatch(events=[_event(1), _event(2)])


def test_event_digest_is_canonical_and_stable() -> None:
    assert canonical_json_sha256({"b": 2, "a": 1}) == canonical_json_sha256(
        {"a": 1, "b": 2}
    )


def test_harness_key_is_normalized() -> None:
    snapshot = AgentHostHarnessSnapshot(
        harness_key="Claude_Code",
        display_name="Claude Code",
        adapter_protocol=AgentHostAdapterProtocol.ACP,
        adapter_protocol_version=1,
        adapter_version="1",
        upstream_version="2",
        auth_state="READY",
        health=AgentHostHarnessHealth.READY,
        config_revision="revision",
        fetched_at=NOW,
        stale_after=NOW,
    )
    assert snapshot.harness_key == "claude-code"


def test_recovered_run_can_return_to_running_checkpoint() -> None:
    assert checkpoint_advances(
        AgentHostCheckpoint.RECOVERING,
        AgentHostCheckpoint.RUNNING,
    )


def test_host_status_uses_heartbeat_freshness() -> None:
    assert (
        effective_agent_host_status(
            AgentHostStatus.ONLINE,
            NOW - timedelta(minutes=5),
            now=NOW,
        )
        is AgentHostStatus.OFFLINE
    )
    assert (
        effective_agent_host_status(
            AgentHostStatus.ONLINE,
            NOW - timedelta(seconds=5),
            now=NOW,
        )
        is AgentHostStatus.ONLINE
    )
    assert (
        effective_agent_host_status(
            AgentHostStatus.REVOKED,
            None,
            now=NOW,
        )
        is AgentHostStatus.REVOKED
    )
