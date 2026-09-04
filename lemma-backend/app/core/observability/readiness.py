"""What "ready" means, apart from how a process runs the probes.

``/health/ready`` answers one question — may this process be sent work it can
actually complete — and the answer is a fold over several components that fail
in different shapes: a dependency is reachable or it is not, a worker is alive
or stalled or absent, a schema is current or behind. Building the payload beside
the probes meant every new component added another conditional to an endpoint
already carrying six, and the two that were missing (PS-OPS-030 names the
SuperTokens core, and a pending migration is the difference between a process
that is running and one that can work) were each a handful of them.

So: the endpoint runs the probes, because only it knows the deadline and the
task group; this decides what their answers mean.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: The worker state that means "running, and not doing the work". Anything else
#: the watchdog reports is a worker that is keeping up.
_WORKER_STALLED = "stalled"

#: A schema older than the code is the case PS-OPS-030 is about: the process is
#: up, and every request touching a new column fails as though the application
#: were buggy. ``unknown`` does not block -- a question that could not be asked
#: is not an answer, and the ``db`` probe already covers a database that is
#: simply broken.
_SCHEMA_PENDING = "pending"


@dataclass(frozen=True, slots=True)
class ComponentState:
    """One line of the readiness answer: what to report, and whether it blocks.

    The two are separate because they genuinely differ. ``migrations: unknown``
    is worth reporting and must not take the process out of rotation; a worker
    that is ``stalled`` must.
    """

    status: str
    ready: bool


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    payload: dict[str, object]
    status_code: int


def dependency_state(healthy: bool) -> ComponentState:
    """A component that is either reachable within its budget, or is not."""
    return ComponentState("ok" if healthy else "down", healthy)


def worker_state(reported: str | None) -> ComponentState | None:
    """The worker's row, or ``None`` when there is no worker to ask about.

    ``worker_readiness_state`` answers ``None`` for "the question does not apply
    to this topology", which is not a verdict and must not appear in the payload
    as one.
    """
    if reported is None:
        return None
    return ComponentState(reported, reported != _WORKER_STALLED)


def migrations_state(reported: str) -> ComponentState:
    """The schema's row: only ``pending`` refuses work. See ``_SCHEMA_PENDING``."""
    return ComponentState(reported, reported != _SCHEMA_PENDING)


def build_readiness_report(
    *,
    components: Mapping[str, ComponentState | None],
    instance_id: str | None,
) -> ReadinessReport:
    """Fold the components into the body and status code to answer with.

    Ready means every component that has an opinion says so. Components whose
    state is ``None`` are dropped rather than reported unknown: the payload is
    read by probes and by people, and a row that means "not applicable here"
    reads as a failure to both.
    """
    reported = {name: state for name, state in components.items() if state is not None}
    ready = all(state.ready for state in reported.values())
    payload: dict[str, object] = {
        "status": "ready" if ready else "not_ready",
        "components": {name: state.status for name, state in reported.items()},
    }
    if instance_id:
        payload["instance_id"] = instance_id
    return ReadinessReport(payload=payload, status_code=200 if ready else 503)
