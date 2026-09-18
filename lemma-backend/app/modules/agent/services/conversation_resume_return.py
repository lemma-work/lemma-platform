"""Turning a resolved approval into the tool result the agent will read.

When a person answers an `ask_user` or decides a `request_approval`, the paused
run is already over. Resuming means writing the tool return that run never got,
so the *next* run reads a complete tool call rather than a dangling one -- which
is the only shape pydantic-ai will accept as history.

Three shapes come out of here, and which one depends on what was paused:

* `ask_user` -- the answers, or a dismissal.
* a host permission request -- handed back to the ACP agent that is still
  blocked on it, denials included, or its run sits until the request times out.
* `request_approval` -- and this is the one with teeth, because an approval
  actually *runs* the inner tool, as the user rather than as the agent. That
  principal switch is the whole point of the approval: the agent was refused,
  the person was not.

Split out of `ConversationService` because it is the part with the most branches
and the least to do with conversations -- it needs a unit of work and an agent
repository, and nothing else the service holds.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.vision import resolve_vision_mode
from app.modules.agent.services.vision_service import vision_delegate_available
from app.modules.agent.domain.agent_host_permissions import (
    agent_host_permission_request,
)
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.run_budget_pause import is_budget_pause
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.approval_reconciliation import (
    agent_host_permission_tool_return,
    execute_approved_tool_as_user,
    record_session_approvals,
)
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.agent.services.workspace_location import resolve_workspace_location

logger = get_logger(__name__)


def _budget_decision_return(decision: AgentRunApprovalDecision) -> dict[str, object]:
    """What the model is told after a person answered "keep going?".

    Denial is not a failure and must not read as one: the person made a
    decision, and an agent told its own work "failed" will try to repair
    something that was never broken. It is told to stop and report, which is the
    thing that makes the deny button worth pressing.
    """
    if decision is AgentRunApprovalDecision.DENY:
        return {
            "success": True,
            "message": (
                "A person decided not to continue this run. Stop here. Do not "
                "start any more work — report what you have done so far and "
                "what is left, so somebody can pick it up."
            ),
        }
    return {
        "success": True,
        "message": (
            "A person asked you to keep going, and your allowance has been "
            "renewed. Carry on from where you stopped — but this was a long "
            "run, so prefer the shortest route to a result over starting "
            "anything new."
        ),
    }


class ResumeToolReturnBuilder:
    """Builds the synthesized tool return that unblocks a resumed run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        agent_repository: AgentRepository,
    ) -> None:
        self.uow = uow
        self.agent_repository = agent_repository

    async def build(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        kind: str,
        tool_args: dict[str, object],
        decision: AgentRunApprovalDecision,
        response: dict[str, object],
        paused_agent_run_id: UUID,
        deliver_to_host: bool = True,
        tool_call_id: str | None = None,
    ) -> tuple[str, object]:
        """Return ``(tool_name, tool_result)`` for the synthesized resume message."""
        from app.modules.agent.tools.user_interaction.models import (
            AskUserResponse,
            RequestApprovalResponse,
        )

        if is_budget_pause(tool_args):
            # Before the `agent_host_permission_request` check and before
            # `inner_tool = tool_args.get("tool_name")` below: the card names a
            # tool (`continue_running`) that does not exist, and the executor
            # branch would try to run it.
            return "request_approval", _budget_decision_return(decision)

        if kind == "ask_user":
            if decision == AgentRunApprovalDecision.DENY:
                content = AskUserResponse(
                    success=False,
                    message="User dismissed the questions without answering.",
                )
            else:
                answers: dict[str, object] = {}
                candidate = response.get("answers")
                if isinstance(candidate, dict):
                    answers = candidate
                elif response:
                    answers = response
                content = AskUserResponse(
                    success=True,
                    answers=answers,
                    message="User answered the questions.",
                )
            return "ask_user", content.model_dump(mode="json")

        if kind == "browser_sign_in":
            return "browser_sign_in", await self._browser_sign_in_return(
                tool_args=tool_args,
                decision=decision,
                response=response,
                user_id=user_id,
                conversation_id=conversation.id,
            )

        host_permission = agent_host_permission_request(tool_args)
        if host_permission is not None and deliver_to_host:
            # Checked before the denial branch below: a denial must reach the
            # host too, or its ACP agent sits blocked until the request times
            # out half an hour later.
            return "request_approval", await agent_host_permission_tool_return(
                uow=self.uow,
                request=host_permission,
                agent_run_id=paused_agent_run_id,
                decision=decision,
                response=response,
            )
        if host_permission is not None:
            # Superseding rides the caller's uncommitted transaction. Handing
            # the decision to the host from here would commit a command in a
            # separate transaction that the caller's rollback could not take
            # back. The run this belonged to is over, so there is nothing to
            # unblock; a host still executing an orphaned run is stopped by
            # reconcile_agent_host_dispatch, which cancels it outright.
            return "request_approval", RequestApprovalResponse(
                success=False,
                message="The request was superseded before it was answered.",
                decision=decision,
                executed=False,
                response=response,
            ).model_dump(mode="json")

        inner_tool = str(tool_args.get("tool_name") or "")
        inner_args = tool_args.get("args")
        inner_args = inner_args if isinstance(inner_args, dict) else {}
        if decision == AgentRunApprovalDecision.DENY:
            content = RequestApprovalResponse(
                success=False,
                message=f"User denied running {inner_tool}.",
                decision=decision,
                executed=False,
                response=response,
            )
            return "request_approval", content.model_dump(mode="json")

        if decision == AgentRunApprovalDecision.APPROVE_FOR_SESSION:
            # Beyond the one-off run below, remember the approval so the
            # workload can keep performing this action type in this
            # conversation (the authorizer honors it as an ephemeral grant,
            # which is the only unlock for DESTRUCTIVE_ACTIONS besides an
            # explicit grant). The permission ids ride in the request_approval
            # args, copied by the agent from the denied tool result.
            # Queued, not awaited: a Redis write inline holds a connection
            # inside an open write transaction, and a rollback must not leave an
            # approval standing. Lands before the tool runs because
            # `execute_approved_tool_as_user` commits first -- see
            # `test_a_session_approval_is_recorded_before_the_tool_runs`.
            self.uow.after_commit(
                partial(
                    record_session_approvals,
                    conversation_id=conversation.id,
                    agent_id=conversation.agent_id,
                    tool_args=tool_args,
                    user_id=user_id,
                )
            )

        executed = await self._execute_approved_tool_as_user(
            conversation=conversation,
            user_id=user_id,
            agent_run_id=paused_agent_run_id,
            tool_name=inner_tool,
            args=dict(inner_args),
        )
        if executed["ok"]:
            content = RequestApprovalResponse(
                success=True,
                message=f"Approved; {inner_tool} executed as the user.",
                decision=decision,
                executed=True,
                result=executed["value"],
                response=response,
            )
        else:
            content = RequestApprovalResponse(
                success=False,
                error=f"Approved, but running {inner_tool} failed: {executed['error']}",
                decision=decision,
                executed=False,
                response=response,
            )
        return "request_approval", content.model_dump(mode="json")

    async def _browser_sign_in_return(
        self,
        *,
        tool_args: dict[str, object],
        decision: AgentRunApprovalDecision,
        response: dict[str, object],
        user_id: UUID,
        conversation_id: UUID,
    ) -> dict[str, object]:
        """What the agent is told after somebody answered a sign-in request.

        The card in the conversation answers a sign-in the same way it answers
        an `ask_user`: through the ordinary approval decision, with no second
        endpoint and no state of its own. That matters for more than symmetry
        -- the transcript and the composer both key off the paused tool call,
        so a resolution the client did not make itself is a resolution it
        never learns about, and the card sat there afterwards saying "sign in
        to continue" over a run that had already moved on.

        This used to *capture* here as well: read the browser, decide which
        cookies were the login, encrypt them. That ran after the execution
        claim had been committed, so anything it raised wrote no tool return
        at all -- and a paused call with no return is a conversation nobody
        can get out of. The whole hazard is gone with the capture: the browser
        keeps its own profile, so finishing a sign-in is the person finishing
        it, and there is nothing left here to fail.
        """
        from app.modules.agent.tools.browser.models import BrowserSignInResponse

        origin = str(tool_args.get("origin") or "")

        if decision == AgentRunApprovalDecision.DENY:
            return BrowserSignInResponse(
                success=True,
                outcome="declined",
                origin=origin,
                message=(
                    "The person did not sign in. Do not ask again for this "
                    "site in this run: do the task another way, or stop and "
                    "say what you could not reach."
                ),
            ).model_dump(mode="json")

        # Read from the decision's own payload, the way the `ask_user` branch
        # above reads its answers. The standalone page puts `working` there --
        # what the site looked like straight after the person finished -- and
        # a card answered in the chat has no browser of its own to ask, so its
        # absence means "not checked" rather than "not working".
        checked = "working" in response
        working = bool(response.get("working"))
        note = (
            "The site stopped asking for a login."
            if not checked or working
            else "The site still showed a login form straight afterwards, so "
            "check before relying on it."
        )
        return BrowserSignInResponse(
            success=True,
            outcome="signed_in",
            source="person",
            origin=origin,
            message=f"The person signed in. {note} Open the page again to carry on.",
        ).model_dump(mode="json")

    async def _execute_approved_tool_as_user(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        agent_run_id: UUID,
        tool_name: str,
        args: dict[str, object],
    ) -> dict[str, object]:
        """Run an approved tool with the user's authority; never raise."""
        deps = await self._build_resume_context(
            conversation=conversation,
            user_id=user_id,
            agent_run_id=agent_run_id,
        )
        return await execute_approved_tool_as_user(
            uow=self.uow,
            deps=deps,
            tool_name=tool_name,
            args=args,
        )

    async def _build_resume_context(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        agent_run_id: UUID,
    ):
        """Rebuild the agent run context so an approved tool runs like in-run.

        Mirrors ``AgentRunnerService.execute``'s context build (runtime profile,
        workspace location, configured accounts). Surface delivery context is
        omitted — approval-gated action tools don't deliver to surfaces.
        """
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.agent.infrastructure.repositories import (
            AgentRuntimeProfileRepository,
        )
        from app.modules.agent.services.runtime_profile_service import (
            AgentRuntimeProfileService,
        )
        from app.modules.agent.tools.callable_tool_factory import (
            AgentCallableToolFactory,
        )
        from app.modules.agent.tools.context import ConversationContext
        from app.core.crypto import get_secret_cipher
        from app.modules.agent.services.workspace_location import resolve_pod_cwd

        uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )
        selected_runtime = (
            conversation.agent_runtime
            or agent.agent_runtime
            or await default_agent_runtime_for_pod(self.uow, pod_id=conversation.pod_id)
        )
        async with uow_factory() as uow:
            profile_service = AgentRuntimeProfileService(
                AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher())
            )
            resolved = await profile_service.resolve(
                runtime=selected_runtime,
                organization_id=conversation.organization_id,
                user_id=user_id,
            )
        configured_accounts = await AgentCallableToolFactory(
            uow_factory
        ).resolve_configured_accounts(agent=agent, user_id=user_id)
        workspace_location = resolve_workspace_location(conversation)
        # Resolved exactly as a normal run resolves it. Left unset this defaults
        # to UNAVAILABLE, so an *approved* `view_image` took the delegate branch
        # and told the user "this agent's model cannot read images directly" --
        # on a model that can. Same for `pod_view_document_pages`.
        supports_vision = RuntimeModelCapability.VISION in resolved.capabilities
        return ConversationContext(
            vision_mode=resolve_vision_mode(
                model_supports_vision=supports_vision,
                delegate_model_configured=vision_delegate_available(),
            ),
            user_id=user_id,
            org_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            conversation_id=conversation.id,
            agent_name=agent.name,
            agent_run_id=agent_run_id,
            workload_type="agent",
            workload_id=agent.id,
            is_pod_default_agent=(agent.kind is AgentKind.POD_DEFAULT),
            configured_accounts=configured_accounts,
            runtime_profile=resolved.public_snapshot(),
            runtime_credentials=resolved.credentials or {},
            workspace_id=workspace_location.workspace_id,
            workspace_cwd=workspace_location.cwd,
            workspace_repo=workspace_location.repo,
            pod_cwd=resolve_pod_cwd(conversation),
        )
