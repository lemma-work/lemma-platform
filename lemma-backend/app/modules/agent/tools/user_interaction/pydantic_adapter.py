from __future__ import annotations
from datetime import datetime, timezone

from app.core.config import settings
from agentbox_client import AgentBoxClient
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.value_objects import JsonObject
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
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id
from app.modules.workspace.services.workspace_sandbox_service import WorkspaceSandboxService


async def display_resource(
    ctx: RunContext[BaseAgentContext],
    request: DisplayResourceRequest,
) -> DisplayResourceResponse:
    """
    Display a user-facing resource or richer interaction.

    Prefer this tool whenever the user would benefit from seeing, inspecting, or
    acting on something instead of receiving prose only. Text is good for brief
    narration, but users often want a concrete surface:
    - asking a multiple-choice question: use the `ask_user` tool. For richer or
      free-form structured input (several typed fields at once), render a WIDGET.
    - showing complex data, charts, previews, or custom visuals: render a WIDGET
      instead of dumping dense text.
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
      skill and follow its guidance. A widget can collect structured input and
      submit it back to the chat — use one when `ask_user` choices aren't enough.

    This tool only displays or requests user-facing resources. User approval for
    potentially sensitive local harness actions remains the separate approval flow.
    """
    # Semantic payload validation runs here (not as a raising pydantic validator)
    # so an invalid request comes back as a uniform success:false/error result the
    # model and frontend can both read, rather than a retry / validation error.
    payload_error = validate_display_payload(request)
    if payload_error is not None:
        return DisplayResourceResponse(success=False, error=payload_error)

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
        # it by URL and it can be promoted to an app verbatim. The content lives
        # durably in this tool call's args, addressed by (conversation, tool_call).
        # See docs/app-widget-unification.md.
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
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        get_platform_capabilities,
    )

    caps = get_platform_capabilities(platform)
    if caps is None or caps.is_email:
        return

    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_display_resource_to_surface,
    )

    await deliver_display_resource_to_surface(
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
            message=(
                "This runtime can't run a tool with the user's approval mid-turn. "
                f"Explain what you need to do ({tool_name}) and why it needs their "
                "authority, ask the user to confirm or run it themselves, and "
                "continue once they reply."
            ),
        )
    if not ctx.tool_call_id:
        return RequestApprovalResponse(
            success=False,
            error="request_approval requires a durable tool call id.",
        )

    # Pause the run for the user's decision instead of blocking the worker. The
    # harness already persisted this tool call (tool_name/args/title in its args)
    # for the client to render an approval card. Raising ends the run cleanly
    # (conversation -> WAITING); on submit the approvals endpoint records the
    # decision, runs the approved tool as the user (or denies), and feeds the
    # synthesized RequestApprovalResponse back as this call's return on a fresh
    # run. request_approval therefore runs only once.
    raise AgentInputRequired(ctx.tool_call_id, "request_approval")


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
    options. For free-form structured input (several typed fields at once), render
    a WIDGET with `display_resource` that submits its answers back to the chat.
    Only ask when it changes what you do next — don't ask about things with an
    obvious default; just proceed.
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
            message=(
                "This runtime can't pause to collect a multiple-choice answer. Ask "
                "the user your question(s) directly in your reply and end your turn; "
                "their next message will continue this conversation with the answer."
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
