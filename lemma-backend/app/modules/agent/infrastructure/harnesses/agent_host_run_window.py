"""How long a dispatched Agent Host run may live, and how it ends.

Three separate ceilings decide a run's real lifetime, and they used to
disagree: the worker task that drives it, the credential encrypted into its
START_RUN command, and the deadline we advertise to the host. This module holds
the one number they now agree on and the reasoning behind it, so a change to
any of them has a single obvious place to land.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import AgentEvent
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventNormalizer,
    error_event,
)

logger = get_logger(__name__)

# The deadline we hand the host, and therefore the real lifetime of the run.
# It is bounded from above by two things that are not negotiable:
#
#   * the worker task that drives the harness (AGENT_RUN_JOB_TIMEOUT_SECONDS,
#     55 minutes). Past that, streaq cancels the job and Lemma reports the run
#     failed - so a deadline beyond it is a deadline we cannot honour, and the
#     host would keep executing a run we have already given up on;
#   * the MCP credential encrypted into START_RUN, which the SuperTokens core
#     issues with a one-hour validity and which nothing refreshes.
#
# 50 minutes sits inside both with room for the harness to notice its own
# deadline, cancel the host run, and finalize before either ceiling bites. It
# also comfortably exceeds the host's 30-minute permission window, so a
# permission parked mid-run can still be answered before the run is over.
DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS = 3000.0
# Stop this far short of the credential's own expiry, so the last tool call of
# a run is still made with a token that is valid.
CREDENTIAL_SAFETY_MARGIN_SECONDS = 120.0
# A run shorter than this is not worth dispatching, so a nearly-expired
# credential fails at dispatch instead of starting a doomed run.
MINIMUM_RUN_SECONDS = 60.0

DEADLINE_MESSAGE = "Agent Host did not emit a terminal event before the run deadline"
CREDENTIAL_DEADLINE_MESSAGE = (
    "Agent Host did not finish before the run's Lemma credential was due to "
    "expire; the run was stopped rather than continuing without Lemma tools"
)


@dataclass(frozen=True, slots=True)
class DispatchedRun:
    """What dispatch settled on, which the consume loop then has to honour."""

    harness_key: str
    event_timeout_seconds: float
    credential_bounded: bool

    @property
    def deadline_message(self) -> str:
        return (
            CREDENTIAL_DEADLINE_MESSAGE
            if self.credential_bounded
            else DEADLINE_MESSAGE
        )


@dataclass(frozen=True, slots=True)
class LeaseOutcome:
    """What a lease check decided; at most one state is set."""

    seen_at: float | None = None
    terminal_state: AgentHostRunState | None = None
    expired_state: AgentHostRunState | None = None


def credential_bounded_timeout(
    *,
    configured_seconds: float,
    credential_expires_at: datetime | None,
    now: datetime,
    agent_run_id: UUID,
) -> tuple[float, bool]:
    """Shorten the run so it cannot outlive the credential it was given.

    The MCP token is minted once, encrypted into START_RUN once, and used
    verbatim by the host's bridge for the run's whole life. Nothing refreshes
    it, and the bridge has no retry: once it expires every ``lemma_*`` call
    401s, the bridge exits, and the agent loses all Lemma tools without anything
    being reported to anyone. Ending the run at the credential's expiry turns
    that silent loss into an ordinary, explained failure.

    Raises when there is not enough credential left to be worth dispatching, so
    the turn fails at dispatch rather than a minute later for a reason nobody
    can see.
    """
    if credential_expires_at is None:
        return configured_seconds, False
    usable = (
        credential_expires_at - now
    ).total_seconds() - CREDENTIAL_SAFETY_MARGIN_SECONDS
    if usable >= configured_seconds:
        return configured_seconds, False
    if usable < MINIMUM_RUN_SECONDS:
        raise RuntimeError(
            "The Lemma credential for this run expires too soon to dispatch it"
        )
    logger.warning(
        "agent.harnesses.agent_host.run_deadline_capped_by_credential.degraded",
        agent_run_id=str(agent_run_id),
        timeout_seconds=int(usable),
    )
    return usable, True


def failure_events(
    normalizer: AgentHostEventNormalizer,
    agent_run_id: UUID,
    message: str,
) -> list[AgentEvent]:
    """Close whatever the run left open, then fail it."""
    terminal = error_event(agent_run_id, message)
    return [*normalizer.close_outstanding(terminal), terminal]


def terminal_checkpoint_state(
    *,
    lease: object | None,
    seen_at: float | None,
    now: float,
    grace_seconds: float,
) -> tuple[float | None, AgentHostRunState | None]:
    """Detect a lease that terminalized without its required terminal event.

    The grace window exists because the checkpoint and the terminal event
    travel different paths, so the checkpoint can land first.
    """
    if lease is None:
        return None, None
    state = AgentHostRunState(lease.state)
    if state not in TERMINAL_AGENT_HOST_RUN_STATES:
        return None, None
    if seen_at is None:
        return now, None
    if now - seen_at < grace_seconds:
        return seen_at, None
    return seen_at, state


def expiry_message(state: AgentHostRunState) -> str:
    if state is AgentHostRunState.FAILED:
        return "No Agent Host received the run before its wait deadline"
    # DISPATCH_UNKNOWN: the host may already have reached a provider, so the
    # turn is deliberately not repeated.
    return "Agent Host delivery could not be confirmed; the run was not repeated"
