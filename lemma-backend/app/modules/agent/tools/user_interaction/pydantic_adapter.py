from __future__ import annotations

from app.core.config import settings
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision, JsonObject
from app.modules.agent.services.widget_token import widget_serve_path
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_errors import (
    AgentInputRequired,
    safe_described_error,
    safe_error_text,
)
from app.modules.agent.tools.user_interaction.models import (
    AskUserRequest,
    AskUserResponse,
    DisplayResourceRequest,
    DisplayResourceResponse,
    DisplayResourceType,
    RequestApprovalResponse,
    validate_display_payload,
)
from app.core.widget_html_validation import validate_widget_html
from app.composition.agent_workspace import WorkspaceSandboxService


async def display_resource(
    ctx: RunContext[BaseAgentContext],
    request: DisplayResourceRequest,
) -> DisplayResourceResponse:
    """
    Show a pod resource or a rendered view to the user.

    Reach for this whenever the useful answer is more than short prose: several
    values, records, statuses, comparisons, a timeline, or a chart render as a
    WIDGET. After creating or changing a pod resource, display it.

    Set `type` and, for most types, a `name` — omit `name` to show all resources
    of that type. FILE takes a pod `path`, so upload sandbox deliverables with
    `lemma files upload` first; a workspace path is not pod-visible. WIDGET takes
    exactly one of `content` or `public_url`; load the `lemma-widget` skill before
    your first widget. React, routing, or real state means an app.

    This tool displays. `ask_user` collects choices, `request_approval` collects
    permission.
    """
    # Semantic payload validation runs here (not as a raising pydantic validator)
    # so an invalid request comes back as a uniform success:false/error result the
    # model and frontend can both read, rather than a retry / validation error.
    payload_error = validate_display_payload(request)
    if payload_error is not None:
        return DisplayResourceResponse(success=False, error=payload_error)

    if request.type == DisplayResourceType.WIDGET and request.content:
        widget_errors = validate_widget_html(request.content)
        if widget_errors:
            return DisplayResourceResponse(
                success=False,
                error="Invalid WIDGET content: " + " ".join(widget_errors),
            )

    if request.type == DisplayResourceType.BROWSER:
        workspace_service = WorkspaceSandboxService()
        try:
            access = await workspace_service.create_browser_access(
                ctx.deps.user_id,
                ttl_seconds=1800,
            )
        except Exception as exc:
            # Redacted: this becomes the tool return the model reads and the
            # transcript keeps, and the sandbox round-trip that fails here
            # stringifies with the URL it dialled.
            return DisplayResourceResponse(
                success=False,
                error=(
                    f"Failed to create browser display URL: {safe_described_error(exc)}"
                ),
            )
        finally:
            await workspace_service.close()

        response = DisplayResourceResponse(
            success=True,
            message="BROWSER resource ready for display.",
            app="browser",
            url=access.url,
            expires_at=access.expires_at,
        )
        await _maybe_deliver_to_surface(ctx, request, response)
        return response

    if (
        request.type == DisplayResourceType.WIDGET
        and request.content
        and request.content.strip()
    ):
        # An inline-content widget is the same primitive as an app: serve its
        # HTML from the backend (with pod context injected) so the frontend embeds
        # it by URL and it can be promoted to an app. The content lives
        # durably in this tool call's args, addressed by (conversation, tool_call).
        conversation_id = getattr(ctx.deps, "conversation_id", None)
        tool_call_id = ctx.tool_call_id
        if conversation_id and tool_call_id:
            # Canonical, token-less address. The widget serve route is
            # authenticated; the frontend mints a short-lived signed embed URL
            # per view, so this URL is for addressing/non-frontend consumers only.
            base = settings.api_url.rstrip("/")
            response = DisplayResourceResponse(
                success=True,
                message="WIDGET resource ready for display.",
                url=f"{base}{widget_serve_path(conversation_id, tool_call_id)}",
            )
            await _maybe_deliver_to_surface(ctx, request, response)
            return response

    response = DisplayResourceResponse(
        success=True,
        message=f"{request.type.value} resource ready for display.",
    )
    await _maybe_deliver_to_surface(ctx, request, response)
    return response


async def _maybe_deliver_to_surface(
    ctx: RunContext[BaseAgentContext],
    request: DisplayResourceRequest,
    response: DisplayResourceResponse,
) -> None:
    """Put the resource in front of the person, however this surface delivers.

    Branching, by the platform's delivery cardinality rather than by whether it
    happens to be email:
      * not a surface run (web/app/subagent) → nothing to do; the frontend
        renders the persisted tool result.
      * MANY (Slack/Teams/Telegram/WhatsApp) → deliver now, native file or link
        as the surface decides.
      * ONE (email) → hold the file for the single reply, which the reply tool
        drains when it sends.

    That last branch used to `return` here, silently, *after* the tool had
    already told the model "FILE resource ready for display." The model believed
    it had shown the file and the recipient never saw one. ``response`` is
    corrected in place when the resource cannot be held, because a tool that
    reports success it did not have is worse than one that reports failure.

    Best-effort otherwise: a delivery failure never fails the run.
    """
    deps = getattr(ctx, "deps", None)
    if deps is None or not response.success:
        return
    platform = getattr(deps, "surface_platform", None)
    if not platform:
        return

    # Lazy import to avoid an agent -> agent_surfaces module-load cycle.
    from app.composition.agent_surface_runtime import (
        platform_delivers_one_reply,
        platform_supports_chat_delivery,
    )

    if platform_delivers_one_reply(platform):
        _hold_for_the_one_reply(deps, request, response)
        return
    if not platform_supports_chat_delivery(platform):
        return

    from app.composition.agent_surface_runtime import deliver_display_resource

    await deliver_display_resource(
        conversation_id=deps.conversation_id,
        request=request,
        tool_call_id=getattr(ctx, "tool_call_id", None),
        tool_output=response,
    )


def _hold_for_the_one_reply(
    deps: BaseAgentContext,
    request: DisplayResourceRequest,
    response: DisplayResourceResponse,
) -> None:
    """Queue a displayed file onto the single reply, or say why it cannot be.

    Only a FILE has anything to carry into an email. Everything else is a link
    into Lemma, which the agent should be writing into the reply body itself --
    so it is told that rather than being left to think a card went out.
    """
    from app.composition.agent_surface_runtime import hold_display_for_one_reply

    if request.type is not DisplayResourceType.FILE or not request.path:
        response.success = False
        response.message = None
        response.error = (
            "This is an email conversation, so there is nothing to display in. "
            "Put what this resource would have shown into your email reply, and "
            "use display_resource only for files you want attached."
        )
        return
    if not hold_display_for_one_reply(deps.conversation_id, request.path):
        response.success = False
        response.message = None
        response.error = (
            "Too many files are already queued for this email reply. Send the "
            "reply, then show anything further in a new one."
        )
        return
    response.message = (
        f"{request.path} will be attached to your email reply. Send the reply "
        "when you are ready; there is nothing else to do for this file."
    )


async def request_approval(
    ctx: RunContext[BaseAgentContext],
    tool_name: str,
    args: JsonObject,
    title: str,
    reason: str | None = None,
    payload: JsonObject | None = None,
    permission_ids: list[str] | None = None,
) -> RequestApprovalResponse:
    """
    Ask the user to approve an action you lack permission for, then run it.

    Call this when a tool fails with a permission error (403) or the action needs
    the user's authority — deleting data, sending email, a privileged command.
    Describe the FULL action; do not rely on prior context. The run pauses for an
    approval card, and on approval the backend executes the tool with the *user's*
    authority and returns its result here. On denial nothing runs.

    Args:
        tool_name: Tool to run on approval, e.g. "exec_command". Must be one you have.
        args: Complete arguments for that tool, e.g. {"cmd": "lemma records delete orders --id 42"}.
        title: Short user-facing title for the approval card.
        reason: Why this needs approval.
        payload: Extra structured detail for rendering or audit.
        permission_ids: Copy `approval.permission_ids` verbatim from the failed tool
            result. Lets "approve for session" cover these actions for the rest of the
            conversation instead of re-prompting.
    """
    del payload  # rendered from the persisted tool call; not needed at runtime
    deps = ctx.deps
    if deps.agent_run_id is None:
        return RequestApprovalResponse(
            success=False,
            error="request_approval requires an active agent run.",
        )
    if tool_name == "request_approval":
        return RequestApprovalResponse(
            success=False,
            error="request_approval cannot approve itself.",
        )
    if not getattr(deps, "supports_pause_signal", False):
        # Remote harnesses (Codex/Claude-Code/OpenCode) run tools over MCP and own
        # their session, so the run can't pause mid tool-call. Guide the model to
        # the conversational fallback instead of hanging or aborting the run.
        # Parked rather than refused -- see the note on `ask_user` below. The
        # decision reaches this call through the same approvals endpoint every
        # other surface already resolves, and the bridge waits for it.
        if not ctx.tool_call_id:
            return RequestApprovalResponse(
                success=False,
                error="request_approval requires a durable tool call id.",
            )
        auto_approved = await _run_if_exact_match_already_approved(
            deps=deps,
            tool_name=tool_name,
            args=args,
            permission_ids=permission_ids,
        )
        if auto_approved is not None:
            return auto_approved
        return RequestApprovalResponse(
            success=True,
            parked_tool_call_id=ctx.tool_call_id,
            message=f"Waiting for the user's decision on {tool_name}.",
        )
    if not ctx.tool_call_id:
        return RequestApprovalResponse(
            success=False,
            error="request_approval requires a durable tool call id.",
        )

    auto_approved = await _run_if_exact_match_already_approved(
        deps=deps,
        tool_name=tool_name,
        args=args,
        permission_ids=permission_ids,
    )
    if auto_approved is not None:
        return auto_approved

    # Pause the run for the user's decision instead of blocking the worker. The
    # harness already persisted this tool call (tool_name/args/title in its args)
    # for the client to render an approval card. Raising ends the run cleanly
    # (conversation -> WAITING); on submit the approvals endpoint records the
    # decision, runs the approved tool as the user (or denies), and feeds the
    # synthesized RequestApprovalResponse back as this call's return on a fresh
    # run. request_approval therefore runs only once.
    raise AgentInputRequired(ctx.tool_call_id, "request_approval")


async def _run_if_exact_match_already_approved(
    *,
    deps: BaseAgentContext,
    tool_name: str,
    args: JsonObject,
    permission_ids: list[str] | None = None,
) -> RequestApprovalResponse | None:
    """Skip the pause when this exact call was approved for session earlier.

    Returns the synthesized response (already executed) if so, else ``None`` to
    fall through to the normal pause. `exec_command`/`execute_python` have no
    authorization gate at all — request_approval is the only checkpoint that
    exists for them — so this is the sole place their session-approval reuse
    can be honored. See session_approvals.exact_command_permission_id for why
    the match is exact-args-only, never a prefix.

    ``permission_ids`` are recorded here as well, and that is the whole of
    DEV-ACCESS-002. The exact-command key is tool-name-plus-args, so a second
    denial of the *same* call for a *different* permission still matches it and
    takes this path -- and the path used to return before anything recorded the
    new permission. Reading a table needs two permissions and the authorizer
    stops at the first missing one, so the agent looped: approve, still denied,
    approve, still denied, forever. Changing one argument broke the exact match
    and made the identical sequence succeed, which is what made it look like
    magic rather than a bug.
    """
    from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
    from app.core.authorization.session_approvals import (
        exact_command_permission_id,
        has_session_approval,
    )

    workload_actor_id = (
        f"agent:{getattr(deps, 'workload_id', None) or DEFAULT_POD_AGENT_ID}"
    )

    # Every permission this call needs must ALREADY be granted -- the exact
    # command key on its own is not enough. `permission_ids` is a tool argument,
    # so it is whatever the model wrote, and a model that has read a poisoned
    # web page or document writes whatever that page told it to. Recording them
    # here would let one approval of `exec_command echo hi` be replayed with
    # `permission_ids=["pod.delete"]` and silently mint that grant, with no
    # pause and no card: the user approved a harmless command and lost the pod.
    #
    # Requiring instead of widening is also what actually fixes the
    # approve-then-still-denied loop (DEV-ACCESS-002). The loop happened because
    # this path SWALLOWED a second, differently-scoped request. Falling through
    # when something is missing means the user is asked exactly once per new
    # permission, and `record_session_approvals` writes it with a human behind
    # it.
    needed = [
        exact_command_permission_id(tool_name, args),
        *(p for p in (permission_ids or []) if isinstance(p, str) and p),
    ]
    for permission_id in needed:
        if not await has_session_approval(
            session_id=str(deps.conversation_id),
            workload_actor_id=workload_actor_id,
            permission_id=permission_id,
        ):
            return None

    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.domain.value_objects import to_json_value
    from app.modules.agent.tools.approval.executor import ApprovalExecutor

    executor = ApprovalExecutor(SessionUnitOfWorkFactory(async_session_maker))
    try:
        result = await executor.execute_as_user(
            deps=deps, tool_name=tool_name, args=args
        )
    except Exception as exc:  # noqa: BLE001 - reported to the model, not fatal
        # Redacted for the same reason the ordinary approval path is: this text
        # is written into the conversation and replayed into the model, and the
        # tool that just failed was running with the user's authority.
        return RequestApprovalResponse(
            success=False,
            error=(
                f"Auto-approved (session), but running {tool_name} failed: "
                f"{safe_error_text(exc)}"
            ),
            decision=AgentRunApprovalDecision.APPROVE_FOR_SESSION,
            executed=False,
        )
    return RequestApprovalResponse(
        success=True,
        message=(
            f"Auto-approved: you approved this exact {tool_name} call earlier in "
            "this conversation. Executed as you."
        ),
        decision=AgentRunApprovalDecision.APPROVE_FOR_SESSION,
        executed=True,
        result=to_json_value(result),
    )


async def ask_user(
    ctx: RunContext[BaseAgentContext],
    request: AskUserRequest,
) -> AskUserResponse:
    """
    Ask the user one or more multiple-choice questions and wait for their answers.

    Use it for a decision you genuinely cannot infer. Each question carries 2-4
    concrete options; mark the one you recommend. The run pauses, and answers come
    back in `answers` keyed by each question's `header`.

    Prefer this over a prose question whenever the answer is a choice among known
    options. For free-form input, ask in prose and end your turn. Only ask when it
    changes what you do next — otherwise take the obvious default and proceed.
    """
    if not request.questions:
        return AskUserResponse(
            success=False, error="ask_user requires at least one question."
        )
    for question in request.questions:
        if not 2 <= len(question.options) <= 4:
            return AskUserResponse(
                success=False,
                error=(
                    f"Question {question.header!r} must have between 2 and 4 options."
                ),
            )

    deps = ctx.deps
    if deps.agent_run_id is None:
        return AskUserResponse(
            success=False, error="ask_user requires an active agent run."
        )
    if not getattr(deps, "supports_pause_signal", False):
        # Remote harnesses (Codex/Claude-Code/OpenCode) run tools over MCP and own
        # their session, so the run can't pause mid tool-call to collect answers.
        # Guide the model to ask conversationally instead of hanging/aborting.
        # Parked, not refused. A remote harness reaches this tool over MCP and
        # cannot end its own turn from inside a tool call -- but it does not
        # need to. Its bridge holds the MCP response open and waits, which is
        # exactly what the host already does for its own native ACP permission
        # requests, so the model sits inside its turn and the person answers on
        # whichever surface they are already using.
        #
        # Falling back to prose was worse than it looked: the questions stopped
        # rendering as an interaction card, so the choices, the recommended
        # option and the native buttons on Slack/Teams/Telegram all became a
        # paragraph the person had to read and answer in their own words.
        if not ctx.tool_call_id:
            return AskUserResponse(
                success=False, error="ask_user requires a durable tool call id."
            )
        return AskUserResponse(
            success=True,
            parked_tool_call_id=ctx.tool_call_id,
            message="Waiting for the user's answer.",
        )
    if not ctx.tool_call_id:
        return AskUserResponse(
            success=False, error="ask_user requires a durable tool call id."
        )

    # Pause the run for the user's answers instead of blocking the worker. The
    # harness already persisted this tool call (the questions ride in its args)
    # for the client to render. Raising ends the run cleanly (conversation ->
    # WAITING); on submit the approvals endpoint records the answers and starts a
    # fresh run that replays the synthesized AskUserResponse from history. A DENY
    # there means the user dismissed the questions. ask_user runs only once.
    raise AgentInputRequired(ctx.tool_call_id, "ask_user")


user_interaction_toolset = FunctionToolset[BaseAgentContext](
    tools=[display_resource, request_approval, ask_user]
)
