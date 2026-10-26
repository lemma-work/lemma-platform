"""An Agent Host run's "does its own shell replace Lemma's" answer, decided once.

Each fact the decision reads is stated by the test through
``NativeCommandFacts`` rather than patched in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.domain.runtime_profiles import SYSTEM_LEMMA_PROFILE_ID
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.agent_host.control.native_commands import (
    NativeCommandFacts,
    recorded_native_commands,
    settle_native_commands,
)

pytestmark = pytest.mark.asyncio

USER = uuid4()
RUN_HOST = uuid4()
OTHER_MAC = uuid4()
HARNESS = uuid4()


class World:
    """What the decision can see, and the run row it writes to."""

    def __init__(self, *, usable_host: UUID | None, applies: bool = True) -> None:
        self.usable_host = usable_host
        self.applies_now = applies
        self.rows: dict[UUID, dict[str, object]] = {}

    def facts(self) -> NativeCommandFacts:
        async def applies(_conversation: Conversation, _run: AgentRun) -> bool:
            return self.applies_now

        async def harness_host(harness_id: UUID) -> UUID | None:
            return RUN_HOST if harness_id == HARNESS else None

        async def usable_for(user_id: UUID, host_id: UUID) -> bool:
            return user_id == USER and host_id == self.usable_host

        async def recorded(run_id: UUID) -> dict[str, object] | None:
            return self.rows.get(run_id)

        async def record(run_id: UUID, value: dict[str, object]) -> None:
            self.rows[run_id] = value

        return NativeCommandFacts(
            applies=applies,
            harness_host=harness_host,
            usable_for=usable_for,
            recorded=recorded,
            record=record,
        )


def _conversation() -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=USER,
        title="host",
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


_SYSTEM_RUNTIME = AgentRuntimeConfig(profile_id=SYSTEM_LEMMA_PROFILE_ID)


def _run(conversation: Conversation) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        conversation_id=conversation.id,
        started_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        agent_runtime=_SYSTEM_RUNTIME,
    )


async def test_the_answer_is_recorded_and_outlives_a_toggle():
    """Turning host execution off mid-run used to change the toolset under a
    prompt that said otherwise; every later context build reads this back."""
    world = World(usable_host=RUN_HOST)
    conversation = _conversation()
    run = _run(conversation)

    first = await settle_native_commands(
        conversation=conversation,
        agent_run=run,
        harness_id=HARNESS,
        facts=world.facts(),
    )
    world.applies_now = False
    world.usable_host = None
    again = await settle_native_commands(
        conversation=conversation,
        agent_run=run,
        harness_id=HARNESS,
        facts=world.facts(),
    )

    assert first is True
    assert again is True
    assert await recorded_native_commands(run.id, facts=world.facts()) is True
    assert world.rows[run.id] == {"enabled": True, "host_id": str(RUN_HOST)}


async def test_it_asks_about_the_runs_own_host_not_any_host():
    """A person with host execution on one Mac and a run on another: the run's
    agent has no Lemma-confined shell there, so it keeps Lemma's tools."""
    world = World(usable_host=OTHER_MAC)
    conversation = _conversation()

    enabled = await settle_native_commands(
        conversation=conversation,
        agent_run=_run(conversation),
        harness_id=HARNESS,
        facts=world.facts(),
    )

    assert enabled is False


async def test_a_run_with_no_harness_keeps_lemmas_tools():
    world = World(usable_host=RUN_HOST)
    conversation = _conversation()
    run = _run(conversation)

    enabled = await settle_native_commands(
        conversation=conversation, agent_run=run, harness_id=None, facts=world.facts()
    )

    assert enabled is False
    assert world.rows[run.id] == {"enabled": False, "host_id": None}


async def test_a_run_that_decided_nothing_reads_as_undecided():
    world = World(usable_host=RUN_HOST)

    assert await recorded_native_commands(uuid4(), facts=world.facts()) is None
