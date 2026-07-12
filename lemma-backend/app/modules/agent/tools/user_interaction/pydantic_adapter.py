from __future__ import annotations
from datetime import datetime, timezone

from app.core.config import settings
from agentbox_client import AgentBoxClient
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
from app.composition.agent_workspace import (
    WorkspaceSandboxService,
    agentbox_sandbox_id,
)


async def display_resource(
    ctx: RunContext[BaseAgentContext],
    request: DisplayResourceRequest,
) -> DisplayResourceResponse:
    """
    Display a user-facing resource or richer interaction.

    Prefer this tool whenever the useful answer is more than short prose. Text is
    for a single fact, a short explanation, or narration around a concrete surface:
    - asking a multiple-choice question: use the `ask_user` tool. For free-form
      input, ask clearly in prose and continue from the user's next message.
    - showing several values, records, statuses, steps, comparisons, a timeline,
      compact table, preview, or chart: render a WIDGET instead of describing the
      structure in prose.
    - creating or updating pod resources: display the created or changed AGENT,
      FUNCTION, WORKFLOW, APP, SCHEDULE, TABLE, or FILE instead of only saying
      that it was created.
    - showing datastore records or resource lists: display the TABLE or resource
      directly so the user can inspect it.

    Use this for every "show this to the user" action: pod files, datastore
    tables, agents, functions, workflows, apps, schedules, and widgets.

    Examples:
    - BROWSER: set type="BROWSER" only. This returns the same short-lived browser
      URL so the user can see the browser backed by the same user sandbox the agent controls with browser CLI
      commands.
    - FILE: set type="FILE" and path="/me/reports/report.pdf".
      First upload sandbox deliverables into pod files using `lemma files upload`.
      Do not pass private workspace paths.
    - TABLE: set type="TABLE", name="<table_name>", and optional filters. Omit
      name to display all tables. Use query only for read-only SQL against
      RLS-disabled tables.
    - AGENT/FUNCTION/WORKFLOW/APP/SCHEDULE: set type and optional name. Name is
      the unique pod resource name within that type. Omit name to display all
      resources of that type.
    - WIDGET: set type="WIDGET" and provide exactly one of public_url or content.
      Before your first widget in a conversation, silently load the `lemma-widget`
      skill and follow its guidance. Inline widgets use plain HTML/CSS/JS or SVG;
      if the UI needs React, routing, or substantial application state, build a Vite
      app instead. Widgets are display surfaces; use `ask_user` or a normal
      conversational turn when you need input from the user.

    This tool only displays or requests user-facing resources. User approval for
    potentially sensitive local harness actions remains the separate approval flow.
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
        runtime = WorkspaceSandboxService._resolve_runtime()
        sandbox_id = agentbox_sandbox_id(ctx.deps.user_id)
        client = AgentBoxClient(
            base_url=settings.agentbox_api_url,
            api_key=settings.agentbox_api_key,
            timeout_seconds=300.0,
        )
        try:
            await client.ensure_sandbox(
                sandbox_id,
                env={
                    "LEMMA_BASE_URL": (
                        WorkspaceSandboxService.resolve_workspace_api_url_for_runtime(
                            runtime
                        )
                    )
                },
            )
            access = await client.get_app_access_url(
                sandbox_id,
                "browser",
                ttl_seconds=1800,
            )
        except Exception as exc:
            return DisplayResourceResponse(
                success=False,
                error=f"Failed to create browser display URL: {type(exc).__name__}: {exc}",
            )
        finally:
            await client.close()

        response = DisplayResourceResponse(
            success=True,
            message="BROWSER resource ready for display.",
            app=access.app,
            url=access.url,
            expires_at=datetime.fromtimestamp(
                access.expires_at,
                tz=timezone.utc,
            ),
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
    Ask the user to approve running a tool you lack permission for, then run it.

    This is a higher-order gate for sensitive or ungranted actions. When one of
    your tool/CLI/python calls fails with a permission error (403), or you know
    an action needs the user's authority (deleting data, sending email, running
    a privileged command), call this tool with the FULL action you want
    performed. State everything needed to run it — do not rely on prior context.

    The run pauses and the client renders an approval card. If the user approves,
    the backend executes the named tool with the *user's* authority (for CLI and
    python, in a fresh workspace session minted with the user's token in the same
    working directory; for other tools, under the user's permissions) and returns
    the tool's result here. If the user denies, nothing runs.

    Arguments:
    - `tool_name`: the tool to run on approval, e.g. "exec_command",
      "execute_python", "pod_write_record". Must be a tool you already have.
    - `args`: the complete arguments for that tool, e.g.
      {"cmd": "lemma records delete orders --id 42"} or {"code": "..."}.
    - `title`: concise user-facing title for the approval card.
    - `reason`: optional explanation of why this needs approval.
    - `payload`: optional extra structured details for rendering/audit.
    - `permission_ids`: when the action failed with a permission error, copy the
      `approval.permission_ids` list from that failed tool result verbatim. If
      the user picks "approve for session", these action types stay approved for
      you for the rest of this conversation instead of re-prompting every time.

    If the user previously picked "approve for session" for this EXACT
    `tool_name` + `args` pair (not just a similar one — the arguments must match
    verbatim), this call runs immediately with no prompt and no pause: you get
    the result back right away, same as if the user had just approved it again.
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
        # Daemon harnesses (Codex/Claude-Code/OpenCode) run tools over MCP and own
        # their session, so the run can't pause mid tool-call. Guide the model to
        # the conversational fallback instead of hanging or aborting the run.
        return RequestApprovalResponse(
            success=False,
            interaction_fallback=True,
            message=(
                "This runtime can't run a tool with the user's approval mid-turn. "
                f"Explain what you need to do ({tool_name}) and why it needs their "
                "authority, ask the user to confirm or run it themselves, and "
                "continue once they reply."
            ),
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

    workload_actor_id = f"agent:{getattr(deps, 'workload_id', None) or DEFAULT_POD_AGENT_ID}"
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
        result = await executor.execute_as_user(deps=deps, tool_name=tool_name, args=args)
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

    Use this to get a decision or clarification you genuinely cannot infer — for
    example which of several approaches to take, or a missing preference. Present a
    short series of questions, each with 2-4 concrete options, and mark the option
    you recommend (`recommended: true`). The run pauses while the client renders
    the questions; the user picks an option per question (or types their own answer
    via an always-available "Other"), and the chosen answers come back in
    `answers`, keyed by each question's `header`.

    Prefer this over a prose question whenever the answer is a choice among known
    options. For free-form input, ask clearly in prose and end your turn so the
    user's next message can answer. Only ask when it changes what you do next —
    don't ask about things with an obvious default; just proceed.
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
                    f"Question {question.header!r} must have between 2 and 4 "
                    "options."
                ),
            )

    deps = ctx.deps
    if deps.agent_run_id is None:
        return AskUserResponse(
            success=False, error="ask_user requires an active agent run."
        )
    if not getattr(deps, "supports_pause_signal", False):
        # Daemon harnesses (Codex/Claude-Code/OpenCode) run tools over MCP and own
        # their session, so the run can't pause mid tool-call to collect answers.
        # Guide the model to ask conversationally instead of hanging/aborting.
        return AskUserResponse(
            success=False,
            interaction_fallback=True,
            message=(
                "This runtime can't pause to collect a multiple-choice answer. Ask "
                "the user your question(s) directly in your reply and end your turn; "
                "their next message will continue this conversation with the answer."
            ),
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
