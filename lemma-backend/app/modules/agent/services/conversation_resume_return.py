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


class SignInServiceLike(Protocol):
    """The two methods `_capture_for_resume` needs of a sign-in service."""

    async def capture(
        self,
        *,
        user_id: UUID,
        origin: str,
        conversation_id: UUID | None,
        force: bool = False,
    ) -> tuple[bool, str | None]: ...

    async def close(self) -> None: ...


def _build_sign_in_service() -> SignInServiceLike:
    """The real service, with a unit-of-work factory of its own.

    Short-lived units of work inside it: the capture runs while the caller's
    unit of work is open, and a sandbox round trip must not be the thing a
    pooled connection waits on.

    Imported here rather than at module scope to keep the web_login module out
    of the import graph of everything that resumes a run.
    """
    from app.core.api.dependencies import get_uow_factory
    from app.modules.web_login.contracts import SignInService

    return SignInService(get_uow_factory())


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
        *,
        sign_in_service: Callable[[], SignInServiceLike] | None = None,
    ) -> None:
        self.uow = uow
        self.agent_repository = agent_repository
        # Injected rather than constructed inside `_capture_for_resume`, which
        # is what it used to do. A factory seam reached only by patching the
        # name means a test proves nothing about the object production builds,
        # and a rename behind that name lands green -- the thing the
        # in-subject-doubles gate exists to stop.
        self._sign_in_service = sign_in_service or _build_sign_in_service

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

        The capture happens here when nobody has done it already, which is what
        lets the card in the conversation answer a sign-in the same way it
        answers an `ask_user`: through the ordinary approval decision, with no
        second endpoint and no state of its own. That matters for more than
        symmetry -- the transcript and the composer both key off the paused
        tool call, so a resolution the client did not make itself is a
        resolution it never learns about, and the card sat there afterwards
        saying "sign in to continue" over a run that had already moved on.

        The standalone page -- the link that reaches a phone from Slack or
        email -- still captures at the moment the person presses the button,
        because it is the only surface that can tell them "the browser holds
        nothing for this site" while they are still in front of it. It passes
        `saved` along on the decision, and this then has nothing to do.

        Never fatal. A run is allowed to carry on with a browser that is signed
        in and a login that was not kept, and the agent is told exactly that.
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
        # above reads its answers. This used to query a table of this feature's
        # own, keyed by the same tool call -- two stores for two values, and
        # they drifted: the lookup filtered on a status the capture had already
        # moved past, so the agent was told the login had not been kept every
        # single time, including the times it had.
        #
        # `captured` distinguishes "the capture ran and kept nothing" from "no
        # capture has run at all". Only the second is ours to do: a payload
        # that carries the key is the standalone page's answer, and repeating
        # its capture would read a browser the person has already left.
        if "saved" in response:
            saved = bool(response.get("saved"))
            detail = response.get("saved_detail")
            detail = str(detail) if detail else None
        else:
            saved, detail = await self._capture_for_resume(
                user_id=user_id, conversation_id=conversation_id, origin=origin
            )

        kept = (
            "It has been kept, so the next run will not ask."
            if saved
            else f"It was not kept{f' ({detail})' if detail else ''}, so a later "
            "run may ask again."
        )
        return BrowserSignInResponse(
            success=True,
            outcome="signed_in",
            source="person",
            origin=origin,
            saved=saved,
            message=f"The person signed in. {kept} Open the page again to carry on.",
        ).model_dump(mode="json")

    async def _capture_for_resume(
        self, *, user_id: UUID, conversation_id: UUID, origin: str
    ) -> tuple[bool, str | None]:
        """Read the browser and keep the login, for an answer given in the chat.

        Best effort, and never fatal, because of where it runs. The caller has
        already committed the execution claim by the time this is reached
        (`_claim_execution`, committed on its own so a killed worker leaves
        evidence). Raising from here therefore writes no tool return at all --
        and a paused call with no return is a conversation nobody can get out
        of: the composer is locked on the pause, `supersede_stale_pending_
        interactions` only runs when a message is sent, and sending a message
        is what the lock prevents. The card is not even retryable until the
        claim ages out.

        So the failure is absorbed and reported to the agent instead. That is
        not the same as hiding it: `saved=False` travels back with the reason
        in the message, the run carries on with a browser that *is* signed in,
        and what it costs is being asked again next time. The alternative was
        not "fail loudly", it was "fail silently and take the conversation with
        it".
        """
        service = self._sign_in_service()
        try:
            return await service.capture(
                user_id=user_id,
                origin=origin,
                conversation_id=conversation_id,
                # Nobody is waiting to be offered "save anyway": this answer
                # came from the conversation, not from the page that can ask.
                force=True,
            )
        except Exception:
            # Logged whole, at error, with the traceback: `capture` already
            # answers a browser's *expected* failures as `(False, why)`, so
            # anything arriving here is a bug or a store that is down, and it
            # must be visible even though the run is allowed to continue.
            logger.error(
                "agent.sign_in.capture_on_resume_failed",
                conversation_id=str(conversation_id),
                origin=origin,
                exc_info=True,
            )
            return False, "the login could not be read back"
        finally:
            await service.close()

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
