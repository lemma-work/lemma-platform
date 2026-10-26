"""Whether an Agent Host and one of its harnesses can take a run now, in words.

Admission is the last point where a run can be refused before anything starts,
and a refused run produces no events: the sentence returned here is the whole
of what the person reads. So each one names the agent and the computer, and a
remedy the person can carry out on that computer.

Readiness is judged from what the host last reported, so it is checked against
how old that report is: a host not heard from within
``AGENT_HOST_OFFLINE_AFTER_SECONDS`` is offline, and a harness snapshot past its
``stale_after`` says nothing reliable about the agent's sign-in or version.
Queuing a run to either used to leave it waiting out the host-wait timeout
before failing with a sentence that named neither.
"""

from __future__ import annotations

from datetime import datetime

from app.modules.agent.agent_host.domain.host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
)

#: Host states that take a new run. Not DRAINING: a host shut down cleanly, or
#: paused, reports that and stops taking work, so a run admitted to it waited
#: out the whole host-wait timeout. Only a new run is refused here -- a run the
#: host already has keeps its lease (``enqueue_run`` finds it before admission)
#: and its host commands, and finishes or is carried over a restart as before.
_ACCEPTING = frozenset({AgentHostStatus.ONLINE})

#: Each agent's own sign-in command, typed in a terminal on the computer it is
#: on. The same commands the workspace shows (``lemma-frontend``
#: ``data/runtimes.ts``); an agent missing here gets a sentence without one.
SIGN_IN_COMMANDS: dict[str, str] = {
    "claude-code": "claude auth login",
    "codex": "codex login",
    "cursor": "cursor-agent login",
    "opencode": "opencode auth login",
}

#: Where the person asks a computer to look at its agents again. A harness
#: published as signed out stays so until the host probes it, which is
#: otherwise up to its next scheduled refresh.
_CHECK_AGAIN = "press Check again on the Models page"


def host_refusal(
    *,
    host_name: str,
    status: str,
    last_seen_at: datetime | None,
    now: datetime,
) -> str | None:
    """Why this host cannot take a run now, or ``None`` if it can."""
    effective = effective_agent_host_status(status, last_seen_at, now=now)
    if effective in _ACCEPTING:
        return None
    if effective is AgentHostStatus.UPGRADE_REQUIRED:
        return (
            f"{host_name} needs a newer Lemma before it can run agents: update "
            "Lemma on that computer, then try again."
        )
    if effective is AgentHostStatus.REVOKED:
        return f"{host_name} was removed from Lemma, so it cannot run agents."
    if effective is AgentHostStatus.DRAINING:
        return (
            f"{host_name} is shutting down or paused and isn't taking new work: "
            "open Lemma on that computer, or resume it, then try again."
        )
    return (
        f"{host_name} is not connected to Lemma, so nothing can run on it: open "
        "Lemma on that computer, or wake it, then try again."
    )


def harness_refusal(
    *,
    host_name: str,
    harness_name: str,
    harness_key: str,
    health: str,
    stale_after: datetime,
    now: datetime,
) -> str | None:
    """Why this harness cannot take a run now, or ``None`` if it can."""
    agent = f"{harness_name} on {host_name}"
    if stale_after < now:
        return (
            f"{agent} has not been checked recently, so Lemma cannot tell whether "
            f"it is ready: open Lemma on that computer and {_CHECK_AGAIN}, then "
            "try again."
        )
    return health_refusal(agent=agent, harness_key=harness_key, health=health)


def health_refusal(*, agent: str, harness_key: str, health: str) -> str | None:
    """Why a harness in ``health`` cannot take a run, about ``agent``; or ``None``."""
    if health == AgentHostHarnessHealth.READY.value:
        return None
    if health == AgentHostHarnessHealth.AUTH_REQUIRED.value:
        command = SIGN_IN_COMMANDS.get(harness_key)
        how = (
            f"run `{command}` in a terminal on that computer"
            if command
            else "sign in to it in a terminal on that computer"
        )
        return f"{agent} needs signing in: {how}, then {_CHECK_AGAIN} and try again."
    return f"{agent} {_UNREADY.get(health, f'is not ready ({health}).')}"


#: What each other health means to the person, after the agent's name.
_UNREADY: dict[str, str] = {
    AgentHostHarnessHealth.INSTALLING.value: (
        "is still being set up; try again in a minute."
    ),
    AgentHostHarnessHealth.UNSUPPORTED_VERSION.value: (
        "is a version Lemma does not support: update it on that computer, then "
        f"{_CHECK_AGAIN} and try again."
    ),
    AgentHostHarnessHealth.CONFIG_INVALID.value: (
        "rejected its settings: fix them on that computer, then "
        f"{_CHECK_AGAIN} and try again."
    ),
    AgentHostHarnessHealth.PROBE_FAILED.value: (
        "could not be started: check the Lemma app's log on that computer."
    ),
    AgentHostHarnessHealth.DISABLED.value: (
        "is turned off, or that computer no longer reports it: turn it back on "
        "in Lemma there, then try again."
    ),
}
