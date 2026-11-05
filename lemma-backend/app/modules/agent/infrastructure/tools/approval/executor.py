"""Executes an approved tool call: as the agent, lent exactly what was refused.

The agent restates the exact tool + args it wants run in ``request_approval``.
On approval that same tool runs, with exactly those args, through the run's
tool runtime under a context that carries an explicit ``ApprovedExecution``. Every tool runs as the agent, with exactly what the
card showed its person lent on top -- the refusals the server had recorded when
the card went up (``approval.denials.card_lends``), never the ``permission_ids``
the model wrote, and never a refusal recorded after the card was shown:

* in-process tools (pod data, connectors, subagents) build the agent's own
  delegated context with those permissions answered as approved
  (``tool_authorization_context``);
* sandbox commands (exec_command, execute_python) keep the workload's token;
  what that session's ``lemma`` calls were refused is written to the
  session-approval store for the length of the command and withdrawn after it
  (``approval.denials.lent_to_the_sandbox``).

A card can also offer a standing grant (``connectors/grant_offer``): a named
agent refused a pod connector is given ``connector.use`` on it when its person
approves, conferred as that person, before the call runs (``grant_outcome``).

Same conversation/pod/cwd as the original run. The approver is the
conversation's owner -- the person the agent works for. A decision from anyone
else is refused before it is recorded (``ResolvePause``), so it never
reaches here.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.context import (
    ApprovedExecution,
    ToolCaller,
    ToolContext,
)
from app.modules.agent.domain.outsiders import OutsiderRunRefused
from app.core.domain.errors import DomainError
from app.modules.agent.infrastructure.tools.approval.grant_outcome import (
    confer_offered_grant,
)
from app.modules.agent.infrastructure.tools.approval.denials import (
    SANDBOX_COMMAND_TOOLS,
    card_lends,
    lent_to_the_sandbox,
)
from app.modules.agent.infrastructure.tools.authority import workload_actor_id
from app.modules.agent.infrastructure.providers.control_flow import (
    is_control_flow_exception,
)
from app.modules.agent.infrastructure.tools.tool_errors import format_tool_error

logger = get_logger(__name__)

REQUEST_APPROVAL_TOOL_NAME = "request_approval"

NOTHING_RUNS_AS_THE_MEMBER = (
    "Nothing runs with a member's authority in a conversation that answers "
    "people outside the pod."
)


async def run_approved(
    tools: ToolCaller,
    uow_factory: UnitOfWorkFactory,
    *,
    deps: ToolContext,
    tool_name: str,
    args: dict | None,
    approval_id: str | None = None,
) -> object:
    """Run ``tool_name`` as the agent, lent exactly what the card showed.

    ``tools`` is the run's tool runtime: the approved call is looked up there
    by name, validated and run like any other.
    """
    if tool_name == REQUEST_APPROVAL_TOOL_NAME:
        raise ValueError("request_approval cannot approve itself")
    # An approval runs a tool with the approver's own authority. A stranger's
    # run never pauses to ask for one, and nothing may be run as the member
    # in the conversation that answers strangers, whatever recorded it.
    if deps.audience.answers_outsiders:
        raise OutsiderRunRefused(NOTHING_RUNS_AS_THE_MEMBER)

    actor = workload_actor_id(deps)
    # What the card showed, and nothing else. With no card -- an earlier
    # "allow for this conversation" covering this call -- nothing is lent:
    # the session approvals already standing are all it may use.
    card = await card_lends(
        conversation_id=deps.conversation_id,
        approval_id=approval_id,
        person_user_id=deps.user_id,
        workload_actor_id=actor,
        tool_name=tool_name,
    )
    if card.grant is not None:
        try:
            await confer_offered_grant(uow_factory, deps=deps, offer=card.grant)
        except DomainError as refused:
            # The approver could not give what the card offered: nothing
            # runs, and the run hears why.
            return format_tool_error(tool_name, refused)
    # A sandbox command's refusals reach its shell through the store; an
    # in-process tool's through its own context.
    sandbox_lent = (
        card.permission_ids if tool_name in SANDBOX_COMMAND_TOOLS else frozenset()
    )
    approved = ApprovedExecution(
        # The run's user is the conversation's owner: the person the agent
        # works for, and the only one whose decision is recorded.
        approver_user_id=deps.user_id,
        agent_id=deps.workload_id,
        conversation_id=deps.conversation_id,
        tool_name=tool_name,
        approval_id=approval_id,
        permission_ids=card.permission_ids,
    )
    # The approval is explicit on the context; in-process tools read it via
    # `tool_authorization_context`. The workload identity stays: the call
    # runs as the agent, and a refusal inside it is recorded under the
    # agent, where the next approval of this call will look for it.
    approved_ctx = deps.model_copy(update={"approved_execution": approved})

    try:
        async with lent_to_the_sandbox(
            conversation_id=deps.conversation_id,
            person_user_id=deps.user_id,
            workload_actor_id=actor,
            permission_ids=sandbox_lent,
        ):
            result = await tools.call(tool_name, args or {}, approved_ctx)
    except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
        if is_control_flow_exception(exc):
            raise
        # An unknown tool or arguments that do not validate: reported back to
        # the run, not raised into the approval task.
        logger.warning(
            "agent.executor.approved_tool_r_returning_result.degraded",
            exc_info=True,
        )
        _audit(approved, outcome="failed")
        return format_tool_error(tool_name, exc)
    _audit(approved, outcome=_outcome(result))
    return result


def _outcome(result: object) -> str:
    """``failed`` for a tool that reported failure in its result, else ``succeeded``."""
    success = (
        result.get("success")
        if isinstance(result, dict)
        else getattr(result, "success", None)
    )
    return "failed" if success is False else "succeeded"


def _audit(approved: ApprovedExecution, *, outcome: str) -> None:
    """Record that a person's authority was used, and for what.

    The one trace of an elevated action: who lent their authority, to which
    agent, for which tool, and what came of it. Info, because it is the normal
    path -- and never the arguments, which may carry secrets.
    """
    logger.info(
        "agent.approval.executed",
        # Three identifiers, the logging contract's limit: who lent authority,
        # to which agent, where. The approval's own id is in that
        # conversation's tool return.
        conversation_id=str(approved.conversation_id),
        agent_id=str(approved.agent_id) if approved.agent_id else "",
        user_id=str(approved.approver_user_id),
        tool_name=approved.tool_name,
        outcome=outcome,
    )
