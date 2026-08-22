"""Re-aiming a queued START_RUN at the harness revision that exists now.

A dispatched run carries the harness ``config_revision`` that was current when
it was enqueued, and the host refuses any command naming a revision it has
since replaced. That refusal is correct -- the command describes a
configuration the machine no longer has -- but it is not a reason to lose the
run, because the host re-publishes for reasons that have nothing to do with the
person who pressed send: a 15-minute refresh, a coding agent updating itself, a
publish that failed during a backend restart and landed a minute later.

The obvious repair is to mark the rejection retryable, and it is a trap. The
poll hands back whatever is ``QUEUED``, verbatim; nothing re-mints a payload,
and ``AgentHostCommandModel`` has no attempt counter. A command requeued with
the same stale revision is refused again a second later, and again, until its
TTL expires -- a spin, instead of a fast failure.

So this module rewrites the payload before the command goes back on the queue,
and counts its own attempts inside the ``rejection`` JSONB the command already
carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
)
from app.modules.agent.domain.agent_host_selections import (
    AgentHostSelectionRefused,
    carry_agent_host_model,
    carry_agent_host_selections,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostHarnessModel,
)

logger = get_logger(__name__)

# Two re-aims, then the run fails with an explanation. A third attempt has
# never been a different answer: either the revision we read is the one the
# host has, and the first re-mint lands it, or the host is re-publishing faster
# than we can dispatch -- which the 15-minute refresh makes impossible, and
# which more retries would not fix anyway.
MAX_REMINT_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class RemintOutcome:
    """Whether the command can go back on the queue, and why not if it cannot.

    Also the inert answer for every rejection that is not a stale revision, so
    the caller records a receipt the same way whichever it was holding.
    """

    requeue: bool
    attempts: int
    refusal: str | None = None

    @property
    def receipt(self) -> dict[str, int]:
        """What this attempt adds to the command's stored rejection.

        Empty when no re-aim was attempted, so an untouched rejection blob
        stays exactly the shape it has always been.
        """
        return {"remint_attempts": self.attempts} if self.attempts else {}


def remint_attempts(rejection: dict | None) -> int:
    """How many times this command has already been re-aimed."""
    if not isinstance(rejection, dict):
        return 0
    attempts = rejection.get("remint_attempts")
    return attempts if isinstance(attempts, int) and attempts > 0 else 0


def _refusal_for(
    *,
    harness: AgentHostHarnessModel | None,
    command: AgentHostCommandModel,
    payload: dict,
    attempts: int,
) -> str | None:
    """Why this command cannot be re-aimed, or ``None`` if it can.

    Every branch here ends the run, so each one is a sentence someone reads.
    """
    if attempts > MAX_REMINT_ATTEMPTS:
        return (
            "this agent's configuration kept changing while the run was being "
            "handed to it"
        )
    if harness is None or harness.host_id != command.host_id:
        return "this agent is no longer registered on that computer"
    if harness.health != AgentHostHarnessHealth.READY.value:
        # Re-aiming at a harness that cannot take work only moves the failure
        # later, and loses the reason on the way. `AUTH_REQUIRED` in particular
        # is a sentence the user can act on.
        return f"{harness.display_name} is not ready: {harness.health}"
    if harness.config_revision == payload.get("profile_revision"):
        # We already told the host this revision and it refused. Either its
        # publish has not reached us yet or it holds something older; requeuing
        # the identical payload is the spin this module exists to avoid.
        return "this computer and Lemma disagree about the agent's configuration"
    return None


async def remint_for_current_revision(
    session: AsyncSession,
    *,
    command: AgentHostCommandModel,
) -> RemintOutcome:
    """Point a refused START_RUN at the harness as it is published right now.

    Mutates ``command.payload`` in place when it can. Returns whether the
    caller should requeue rather than terminalize, and the attempt number this
    was, so the caller can record it.
    """
    attempts = remint_attempts(command.rejection) + 1
    payload = dict(command.payload or {})
    raw_harness_id = payload.get("harness_id")
    if not raw_harness_id:
        return RemintOutcome(
            requeue=False,
            attempts=attempts,
            refusal="the run was dispatched without a harness",
        )
    harness = await session.get(AgentHostHarnessModel, UUID(str(raw_harness_id)))
    refusal = _refusal_for(
        harness=harness, command=command, payload=payload, attempts=attempts
    )
    if refusal is not None:
        return RemintOutcome(requeue=False, attempts=attempts, refusal=refusal)
    assert harness is not None  # noqa: S101 - narrowed by `_refusal_for`

    config_options = list(harness.config_options or [])
    try:
        carried = carry_agent_host_selections(
            config_options=config_options,
            selections=dict(payload.get("config_selections") or {}),
        )
    except AgentHostSelectionRefused as exc:
        return RemintOutcome(requeue=False, attempts=attempts, refusal=str(exc))

    previous_revision = str(payload.get("profile_revision") or "")
    previous_selections = dict(payload.get("config_selections") or {})
    previous_model = payload.get("model_name")
    carried_model = carry_agent_host_model(
        config_options=config_options,
        model_name=previous_model if isinstance(previous_model, str) else None,
    )
    payload["profile_revision"] = harness.config_revision
    payload["config_selections"] = carried
    payload["model_name"] = carried_model
    command.payload = payload
    logger.info(
        "agent.infrastructure.agent_host_command_remint.reaimed",
        agent_run_id=str(command.run_id) if command.run_id else None,
        host_id=str(command.host_id),
        harness_key=harness.harness_key,
        attempt=attempts,
        previous_revision=previous_revision[:8],
        current_revision=harness.config_revision[:8],
        dropped_selections=sorted(set(previous_selections) - set(carried)) or None,
        model_cleared=bool(previous_model) and carried_model is None,
    )
    return RemintOutcome(requeue=True, attempts=attempts)
