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
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    AgentHostEventNormalizer,
    error_event,
)

logger = get_logger(__name__)

# The deadline we hand the host, and therefore the real lifetime of the run.
#
# This was 50 minutes, and the reason given was the MCP credential encrypted
# into START_RUN: the SuperTokens core issues it with a one-hour validity, and
# "nothing refreshes" it. Something does now -- `refresh_credential` mints a
# replacement and sends it to the host as a command, on the margin below -- so
# the credential stopped being the ceiling and the 50 minutes outlived its own
# justification. What it cost was an agent working steadily on a real task
# being killed at minute fifty with "run deadline elapsed", which is a system
# failure wearing the clothes of a policy.
#
# A long deadline is safe because it is not what detects a host that has gone
# away. The run lease is 90 seconds and is checked every 5, so a host that
# crashed, slept or lost its network is failed inside two minutes no matter how
# far off the deadline is. The deadline exists only to bound a host that is
# still answering and still working.
#
# What it must still sit inside:
#
#   * the worker task that drives the harness
#     (AGENT_RUN_JOB_TIMEOUT_SECONDS). Past that, streaq cancels the job and
#     Lemma reports the run failed -- so a deadline beyond it is one we cannot
#     honour, and the host would keep executing a run we have given up on;
#   * comfortably above the host's 30-minute permission window, so a permission
#     parked mid-run can still be answered before the run is over.
DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS = 14400.0
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


# Renew the run's Lemma credential this long before it expires. Comfortably
# more than one lease-check cycle, so a refresh has several attempts before the
# safety margin below turns a failure into an explained end of run.
CREDENTIAL_REFRESH_MARGIN_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class DispatchedRun:
    """What dispatch settled on, which the consume loop then has to honour."""

    harness_key: str
    event_timeout_seconds: float
    credential_bounded: bool
    credential_expires_at: datetime | None = None

    @property
    def deadline_message(self) -> str:
        return (
            CREDENTIAL_DEADLINE_MESSAGE if self.credential_bounded else DEADLINE_MESSAGE
        )


def credential_refresh_due(
    *,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    """Whether the run's Lemma credential is close enough to expiry to renew."""
    if expires_at is None:
        return False
    return (expires_at - now).total_seconds() <= CREDENTIAL_REFRESH_MARGIN_SECONDS


def credential_exhausted(
    *,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    """Whether the credential is too close to expiry for the run to continue.

    The backstop for a refresh that never landed. Ending the run here turns
    what would otherwise be every Lemma tool silently 401ing — which the agent
    experiences as its tools vanishing mid-task — into an ordinary, explained
    failure.
    """
    if expires_at is None:
        return False
    return (expires_at - now).total_seconds() <= CREDENTIAL_SAFETY_MARGIN_SECONDS


@dataclass(frozen=True, slots=True)
class LeaseOutcome:
    """What a lease check decided; at most one state is set."""

    seen_at: float | None = None
    terminal_state: AgentHostRunState | None = None
    expired_state: AgentHostRunState | None = None
    #: The sentence the lease recorded for why it ended, when it has one.
    #: Carried alongside ``terminal_state`` because the lease row is where a
    #: pre-dispatch refusal writes its reason, and the run it refused has no
    #: events of its own to carry it.
    terminal_detail: str | None = None


def credential_bounded_timeout(
    *,
    configured_seconds: float,
    credential_expires_at: datetime | None,
    now: datetime,
    agent_run_id: UUID,
) -> tuple[float, bool]:
    """Refuse to dispatch a run whose credential is already spent.

    This used to also *shorten* the run to the credential's remaining life. That
    made sense while the token was the run's real ceiling, and it was harmless
    while the window was fifty minutes, because a fresh one-hour token outlived
    it and the clamp never fired.

    It is neither now. The consume loop renews the credential in flight
    (:func:`credential_refresh_due`), so the token dispatched with the run is a
    starting point rather than a limit -- and against a window measured in hours
    a clamp to the *initial* expiry would fire on every single run, quietly
    putting back the ceiling this was supposed to have lifted.

    So the expiry no longer bounds the window. What it still does is refuse to
    start a run there is not enough credential left to be worth starting, and
    the run that outlives every attempt to renew is ended explicitly by
    :func:`credential_exhausted` rather than left to discover its tools have
    stopped working.
    """
    if credential_expires_at is None:
        # An opaque credential silently disables all three of the protections
        # in this module: nothing schedules a refresh, nothing caps the
        # deadline, and `credential_exhausted` can never fire. The run then
        # outlives its token and the agent experiences its Lemma tools failing
        # one by one, which is exactly the failure the rest of this file was
        # written to prevent. Said out loud, because the alternative is
        # diagnosing it from a transcript.
        logger.warning(
            "agent.harnesses.agent_host.credential_expiry_unknown.degraded",
            agent_run_id=str(agent_run_id),
        )
        return configured_seconds, False
    usable = (
        credential_expires_at - now
    ).total_seconds() - CREDENTIAL_SAFETY_MARGIN_SECONDS
    if usable < MINIMUM_RUN_SECONDS:
        raise RuntimeError(
            "The Lemma credential for this run expires too soon to dispatch it"
        )
    return configured_seconds, False


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


def lease_terminal_detail(lease: object | None) -> str | None:
    """Why this lease ended, in the words it recorded, or ``None``.

    Only ever a sentence written for a person: every writer of
    ``error_detail`` -- the rejection receipt in ``control_updates`` and the
    two recovery paths -- puts one there. That matters because this is the
    text a run refused *before it started* ends on, and the alternative is
    ``finish_without_terminal``'s description of Lemma's own plumbing.

    Reads the attribute rather than the model, like
    :func:`terminal_checkpoint_state` above it, so this module stays a pure
    helper over whatever the repository hands back.
    """
    detail = getattr(lease, "error_detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return None


def expiry_message(state: AgentHostRunState) -> str:
    if state is AgentHostRunState.FAILED:
        return "No Agent Host received the run before its wait deadline"
    # DISPATCH_UNKNOWN: the host may already have reached a provider, so the
    # turn is deliberately not repeated.
    return "Agent Host delivery could not be confirmed; the run was not repeated"
