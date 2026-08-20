from __future__ import annotations

from app.core.config import settings
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision, JsonObject
from app.modules.agent.services.widget_token import widget_serve_path
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_errors import AgentInputRequired
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
    WIDGET instead of being described. After creating or changing a pod resource,
    display it rather than only saying it was created.

    Set `type` and, for most types, a `name` — omit `name` to show all resources
    of that type. FILE takes a pod `path`, so upload sandbox deliverables with
    `lemma files upload` first and never pass a workspace path. WIDGET takes
    exactly one of `content` or `public_url`; load the `lemma-widget` skill before
    your first widget, and build an app instead when the UI needs React, routing,
    or real state.

    This tool only displays. Use `ask_user` for choices and `request_approval` for
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
            return DisplayResourceResponse(
                success=False,
                error=f"Failed to create browser display URL: {type(exc).__name__}: {exc}",
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
    """Deliver the resource to the chat surface when running on one.

    Branching:
      * not a surface run (web/app/subagent) → do nothing; the frontend renders
        the persisted tool result.
      * email surface (Gmail/Outlook) → do nothing; the run observer accumulates
        display resources into the single composed email reply.
      * chat surface (Slack/Teams/Telegram/WhatsApp) → deliver now (native file
        / link decided by the surface).

    Best-effort: a delivery failure never fails the tool or the run.
    """
    deps = getattr(ctx, "deps", None)
    if deps is None or not response.success:
        return
    platform = getattr(deps, "surface_platform", None)
    if not platform:
        return

    # Lazy import to avoid an agent -> agent_surfaces module-load cycle.
    from app.composition.agent_surface_runtime import platform_supports_chat_delivery

    if not platform_supports_chat_delivery(platform):
        return

    from app.composition.agent_surface_runtime import deliver_display_resource

    await deliver_display_resource(
        conversation_id=deps.conversation_id,
        request=request,
        tool_call_id=getattr(ctx, "tool_call_id", None),
        tool_output=response,
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
    del permission_ids  # read from the persisted tool call on resolution
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
            deps=deps, tool_name=tool_name, args=args
        )
        if auto_approved is not None:
            return auto_approved
        return RequestApprovalResponse(
            success=True,
            parked_tool_call_id=ctx.tool_call_id,
            message=f"Waiting for the user's decision on {tool_name}.",
        )
    # Email surfaces are non-interactive — they can't pause for an approve/deny
    # reply, and pausing would strand the run in WAITING with nothing delivered.
    # Fail fast so the model proceeds and delivers via the email reply tool.
    from app.composition.agent_surface_runtime import platform_is_email

    if platform_is_email(getattr(deps, "surface_platform", None)):
        return RequestApprovalResponse(
            success=False,
            interaction_fallback=True,
            message=(
                "This is an email conversation — it can't pause for an approval. "
                f"Explain in your reply what you want to do ({tool_name}) and why "
                "it needs their authority, ask them to confirm by replying, and "
                "deliver everything through the email reply tool. Do not call "
                "request_approval here."
            ),
        )
    if not ctx.tool_call_id:
        return RequestApprovalResponse(
            success=False,
            error="request_approval requires a durable tool call id.",
        )

    auto_approved = await _run_if_exact_match_already_approved(
        deps=deps, tool_name=tool_name, args=args
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
) -> RequestApprovalResponse | None:
    """Skip the pause when this exact call was approved for session earlier.

    Returns the synthesized response (already executed) if so, else ``None`` to
    fall through to the normal pause. `exec_command`/`execute_python` have no
    authorization gate at all — request_approval is the only checkpoint that
    exists for them — so this is the sole place their session-approval reuse
    can be honored. See session_approvals.exact_command_permission_id for why
    the match is exact-args-only, never a prefix.
    """
    from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
    from app.core.authorization.session_approvals import (
        exact_command_permission_id,
        has_session_approval,
    )

    workload_actor_id = (
        f"agent:{getattr(deps, 'workload_id', None) or DEFAULT_POD_AGENT_ID}"
    )
    approved = await has_session_approval(
        session_id=str(deps.conversation_id),
        workload_actor_id=workload_actor_id,
        permission_id=exact_command_permission_id(tool_name, args),
    )
    if not approved:
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
        return RequestApprovalResponse(
            success=False,
            error=f"Auto-approved (session), but running {tool_name} failed: {exc}",
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
    # Email surfaces are non-interactive — they can't pause for an answer, and
    # pausing would strand the run in WAITING with nothing delivered. Fail fast so
    # the model inlines the question (or picks a sensible default) and continues.
    from app.composition.agent_surface_runtime import platform_is_email

    if platform_is_email(getattr(deps, "surface_platform", None)):
        return AskUserResponse(
            success=False,
            interaction_fallback=True,
            message=(
                "This is an email conversation — it can't pause for a "
                "multiple-choice answer. Ask your question(s) directly in your "
                "reply (or pick the most sensible default and proceed), then "
                "deliver everything through the email reply tool."
            ),
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
