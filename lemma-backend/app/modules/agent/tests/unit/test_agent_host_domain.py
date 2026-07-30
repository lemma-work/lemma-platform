"""Unit contract for Agent Host wire/domain types."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostCommand,
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostHarnessHealth,
    AgentHostHarnessSnapshot,
    AgentHostRunState,
    AgentHostStatus,
    HostHello,
    effective_agent_host_status,
    run_state_progresses,
)


NOW = datetime.now(timezone.utc)


def test_hello_negotiates_only_the_matching_protocol() -> None:
    hello = HostHello(
        installation_id="installation",
        host_release="2026.8.0",
        protocol_version=AGENT_HOST_PROTOCOL_VERSION,
    )

    assert hello.negotiate() == AGENT_HOST_PROTOCOL_VERSION

    incompatible = hello.model_copy(update={"protocol_version": 99})
    with pytest.raises(ValueError, match="does not match"):
        incompatible.negotiate()


def test_command_requires_run_fencing_for_run_commands() -> None:
    with pytest.raises(ValidationError, match="requires run_id and lease_epoch"):
        AgentHostCommand(
            command_id=uuid4(),
            kind=AgentHostCommandKind.START_RUN,
            created_at=NOW,
            expires_at=NOW,
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


def test_harness_key_is_normalized() -> None:
    snapshot = AgentHostHarnessSnapshot(
        harness_key="Claude_Code",
        display_name="Claude Code",
        adapter_version="1",
        upstream_version="2",
        health=AgentHostHarnessHealth.READY,
        config_revision="revision",
        stale_after=NOW,
    )
    assert snapshot.harness_key == "claude-code"


def test_run_state_progression_rules() -> None:
    assert run_state_progresses(
        AgentHostRunState.LEASED,
        AgentHostRunState.ACCEPTED,
    )
    assert run_state_progresses(
        AgentHostRunState.QUEUED_FOR_HOST,
        AgentHostRunState.RUNNING,
    )
    assert not run_state_progresses(
        AgentHostRunState.LEASED,
        AgentHostRunState.LEASED,
    )
    assert not run_state_progresses(
        AgentHostRunState.RUNNING,
        AgentHostRunState.ACCEPTED,
    )
    assert run_state_progresses(
        AgentHostRunState.RUNNING,
        AgentHostRunState.RECOVERING,
    )
    assert run_state_progresses(
        AgentHostRunState.RECOVERING,
        AgentHostRunState.RUNNING,
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
