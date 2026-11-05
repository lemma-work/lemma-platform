"""What admission tells a person when their computer or its agent cannot run.

A refused run produces no events, so each sentence is the whole of what the
person reads: it has to name the agent and the computer, and a fix they can
carry out there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.agent.agent_host.domain.host import (
    AGENT_HOST_OFFLINE_AFTER_SECONDS,
    AgentHostHarnessHealth,
    AgentHostStatus,
)
from app.modules.agent.agent_host.domain.readiness import (
    harness_refusal,
    host_refusal,
)

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
FRESH = NOW + timedelta(hours=23)


def _host(status: AgentHostStatus, *, seen_ago: float | None = 5) -> str | None:
    return host_refusal(
        host_name="Ada's MacBook",
        status=status.value,
        last_seen_at=None if seen_ago is None else NOW - timedelta(seconds=seen_ago),
        now=NOW,
    )


def _harness(
    health: AgentHostHarnessHealth,
    *,
    key: str = "claude-code",
    stale_after: datetime = FRESH,
) -> str | None:
    return harness_refusal(
        host_name="Ada's MacBook",
        harness_name="Claude Code",
        harness_key=key,
        health=health.value,
        stale_after=stale_after,
        now=NOW,
    )


def test_a_host_heard_from_lately_takes_runs():
    assert _host(AgentHostStatus.ONLINE) is None


def test_a_draining_host_takes_no_new_run():
    """A host shut down cleanly reports DRAINING and stops taking work. A run
    admitted to it waited out the whole host-wait timeout, then failed naming
    neither the computer nor a fix."""
    refusal = _host(AgentHostStatus.DRAINING)

    assert refusal is not None
    assert "Ada's MacBook is shutting down or paused" in refusal
    assert "open Lemma on that computer" in refusal


@pytest.mark.parametrize("seen_ago", [AGENT_HOST_OFFLINE_AFTER_SECONDS + 1, 3600, None])
def test_a_host_not_heard_from_is_refused_at_admission(seen_ago):
    """It used to be queued anyway and fail minutes later, naming nothing."""
    refusal = _host(AgentHostStatus.ONLINE, seen_ago=seen_ago)

    assert refusal is not None
    assert "Ada's MacBook is not connected to Lemma" in refusal
    assert "open Lemma on that computer" in refusal


def test_a_host_reported_offline_is_refused_however_recent():
    assert _host(AgentHostStatus.OFFLINE) is not None


def test_a_host_that_needs_an_update_says_so():
    refusal = _host(AgentHostStatus.UPGRADE_REQUIRED)

    assert refusal is not None and "update Lemma on that computer" in refusal


def test_a_signed_out_agent_names_the_command_that_signs_it_in():
    refusal = _harness(AgentHostHarnessHealth.AUTH_REQUIRED)

    assert refusal == (
        "Claude Code on Ada's MacBook needs signing in: run `claude auth login` "
        "in a terminal on that computer, then press Check again on the Models "
        "page and try again."
    )
    assert "AUTH_REQUIRED" not in refusal


def test_a_signed_out_agent_without_a_known_command_is_still_told_what_to_do():
    refusal = _harness(AgentHostHarnessHealth.AUTH_REQUIRED, key="some-new-agent")

    assert refusal is not None
    assert "sign in to it in a terminal on that computer" in refusal


def test_a_stale_snapshot_is_refused_even_when_it_last_said_ready():
    """``stale_after`` was stored and never read: a READY published a day ago
    said nothing about whether the agent is still signed in."""
    refusal = _harness(
        AgentHostHarnessHealth.READY, stale_after=NOW - timedelta(seconds=1)
    )

    assert refusal is not None
    assert "has not been checked recently" in refusal
    assert "Claude Code on Ada's MacBook" in refusal


def test_a_fresh_ready_snapshot_is_admitted():
    assert _harness(AgentHostHarnessHealth.READY) is None


@pytest.mark.parametrize(
    "health",
    [
        health
        for health in AgentHostHarnessHealth
        if health is not AgentHostHarnessHealth.READY
    ],
)
def test_no_unready_health_reaches_the_person_as_a_raw_code(health):
    refusal = _harness(health)

    assert refusal is not None
    assert refusal.startswith("Claude Code on Ada's MacBook ")
    assert health.value not in refusal
