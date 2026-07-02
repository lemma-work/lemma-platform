from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.modules.agent.tools.user_interaction.pydantic_adapter as user_interaction_adapter
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentToolset,
    ConnectorAccessConfig,
    ConnectorMode,
    ConversationType,
    HarnessOptions,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.history import build_history_processors
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.services.agent_runner_service import (
    FULL_HISTORY_AGENT_RUN_COUNT,
    AgentRunnerService,
)
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.final_answer.final_answer_tool import FinalAgentResult
from app.modules.agent.tools.pod import pod_toolset
from app.modules.agent.tools.registry import (
    POD_DEFAULT_AGENT_TOOLSETS,
    resolve_agent_toolsets,
)
from app.modules.agent.tools.user_interaction import user_interaction_toolset
from app.modules.agent.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
    validate_display_payload,
)
from app.modules.agent.tools.subagents.pydantic_adapter import subagents_toolset
from app.modules.agent.tools.tool_errors import AgentInputRequired
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    ask_user,
    display_resource,
    request_approval,
)
from app.modules.agent.tools.web.pydantic_adapter import web_search_toolset
from app.modules.agent.tools.workspace_cli import workspace_cli_toolset
from app.modules.function.domain.entities import FunctionEntity, FunctionType


def _agent_run_with_messages(run_index: int, message_count: int = 5) -> AgentRun:
    conversation_id = uuid4()
    run_id = uuid4()
    return AgentRun(
        id=run_id,
        conversation_id=conversation_id,
        agent_runtime=AgentRuntimeConfig(
            profile_id="system:lemma",
            model_name="kimi-k2",
        ),
        started_at=datetime.now(timezone.utc),
        messages=[
            Message(
                conversation_id=conversation_id,
                sequence=(run_index * 100) + message_index,
                agent_run_id=run_id,
                role=(
                    MessageRole.USER.value
                    if message_index == 0
                    else MessageRole.ASSISTANT.value
                ),
                kind=MessageKind.TEXT,
                text=f"run {run_index} message {message_index}",
            )
            for message_index in range(message_count)
        ],
    )


def _messages_by_run(messages: list[Message]) -> dict[UUID, list[Message]]:
    grouped: dict[UUID, list[Message]] = {}
    for message in messages:
        assert message.agent_run_id is not None
        grouped.setdefault(message.agent_run_id, []).append(message)
    return grouped


def test_toolset_resolver_returns_exactly_the_selected_toolsets():
    # No implicit defaults: the resolver returns exactly what it is given,
    # deduplicated and order-preserving.
    toolsets = resolve_agent_toolsets(
        [
            AgentToolset.SPEECH,
            AgentToolset.WORKSPACE_CLI,
            AgentToolset.SPEECH,
        ]
    )

    assert len(toolsets) == 2
    assert toolsets[0] is not toolsets[1]
    assert all(item.__class__.__name__.endswith("Toolset") for item in toolsets)


@pytest.mark.asyncio
async def test_default_pod_agent_gets_fixed_default_toolsets():
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=None,
    )

    agent = await runner._resolve_agent(
        uow=object(),
        conversation=conversation,
        user_id=conversation.user_id,
        agent_name=None,
    )
    toolsets = await runner.tool_assembler.assemble(
        agent=agent,
        conversation=conversation,
    )

    # The pod default assistant gets the fixed batteries-included set:
    # workspace CLI, pod, user-interaction, skills, and web search.
    assert agent.toolsets == list(POD_DEFAULT_AGENT_TOOLSETS)
    assert user_interaction_toolset in toolsets
    assert pod_toolset in toolsets
    assert workspace_cli_toolset in toolsets
    assert web_search_toolset in toolsets
    # The pod default assistant can orchestrate sub-agents by default.
    assert subagents_toolset in toolsets


@pytest.mark.asyncio
async def test_user_created_agent_gets_only_its_selected_toolsets():
    # A user-created agent must get EXACTLY the toolsets it was created with —
    # the pod defaults (workspace CLI, skills, …) are NOT forced onto it.
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    agent = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="reporter",
        instruction="Summarize records.",
        toolsets=[AgentToolset.POD, AgentToolset.WEB_SEARCH],
    )
    conversation = Conversation(
        pod_id=agent.pod_id,
        user_id=agent.user_id,
        agent_id=agent.id,
    )

    toolsets = await runner.tool_assembler.assemble(
        agent=agent,
        conversation=conversation,
    )

    assert pod_toolset in toolsets
    assert web_search_toolset in toolsets
    # Nothing from the pod defaults is implicitly added — including SUBAGENTS,
    # which is opt-in for user-created agents.
    assert workspace_cli_toolset not in toolsets
    assert user_interaction_toolset not in toolsets
    assert subagents_toolset not in toolsets


@pytest.mark.asyncio
async def test_todo_toolset_gated_by_agent_definition(monkeypatch):
    # RunToolAssembler feeds BOTH the in-process harness and the daemon MCP path,
    # so a user-created agent gets the todo tools only when its toolsets include
    # TODO — never implicitly.
    from app.modules.agent.tools import callable_tool_factory as ctf

    async def _no_dynamic(self, *, agent, allow_subagents):  # noqa: ANN001
        return []

    monkeypatch.setattr(ctf.AgentCallableToolFactory, "build_toolsets", _no_dynamic)

    runner = AgentRunnerService(uow_factory=lambda: None, harness_registry=object())

    def _has_todo(toolsets) -> bool:
        return any(getattr(t, "id", None) == "lemma_todo" for t in toolsets)

    without_todo = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="no_todo",
        instruction="x",
        toolsets=[AgentToolset.WORKSPACE_CLI],
    )
    conv = Conversation(
        pod_id=without_todo.pod_id,
        user_id=without_todo.user_id,
        agent_id=without_todo.id,
    )
    assert not _has_todo(
        await runner.tool_assembler.assemble(agent=without_todo, conversation=conv)
    )

    with_todo = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="todo",
        instruction="x",
        toolsets=[AgentToolset.WORKSPACE_CLI, AgentToolset.TODO],
    )
    conv2 = Conversation(
        pod_id=with_todo.pod_id,
        user_id=with_todo.user_id,
        agent_id=with_todo.id,
    )
    assert _has_todo(
        await runner.tool_assembler.assemble(agent=with_todo, conversation=conv2)
    )


@pytest.mark.asyncio
async def test_display_resource_handles_file_path():
    response = await display_resource(
        None,  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/report.pdf"),
    )

    assert response.success is True
    assert response.message == "FILE resource ready for display."


def _surface_ctx(platform: str | None):
    return SimpleNamespace(
        tool_call_id="tc-1",
        deps=SimpleNamespace(
            surface_platform=platform,
            conversation_id=uuid4(),
        ),
    )


def _patch_surface_delivery(monkeypatch):
    """Capture deliver_display_resource_to_surface calls (lazily imported in the tool)."""
    import app.modules.agent_surfaces.services.surface_display_delivery as sdd

    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(sdd, "deliver_display_resource_to_surface", _fake)
    return calls


@pytest.mark.asyncio
async def test_display_resource_delivers_to_chat_surface(monkeypatch):
    calls = _patch_surface_delivery(monkeypatch)
    ctx = _surface_ctx("SLACK")

    response = await display_resource(
        ctx,  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    assert response.success is True
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == ctx.deps.conversation_id
    assert calls[0]["tool_call_id"] == "tc-1"
    assert calls[0]["request"].type == DisplayResourceType.TABLE


@pytest.mark.asyncio
async def test_display_resource_email_surface_not_delivered_by_tool(monkeypatch):
    # Email surfaces are composed into a single reply by the run observer; the
    # tool must not deliver them immediately.
    calls = _patch_surface_delivery(monkeypatch)
    ctx = _surface_ctx("GMAIL")

    response = await display_resource(
        ctx,  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/report.pdf"),
    )

    assert response.success is True
    assert calls == []


@pytest.mark.asyncio
async def test_display_resource_no_surface_does_not_deliver(monkeypatch):
    # Web/app/subagent runs carry no surface_platform → pure return, no send.
    calls = _patch_surface_delivery(monkeypatch)
    ctx = SimpleNamespace(tool_call_id="tc-1", deps=SimpleNamespace(conversation_id=uuid4()))

    response = await display_resource(
        ctx,  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    assert response.success is True
    assert calls == []


@pytest.mark.asyncio
async def test_display_resource_returns_browser_access_url(monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    calls: list[tuple[str, object]] = []

    class FakeAgentBoxClient:
        def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float):
            calls.append(("init", (base_url, api_key, timeout_seconds)))

        async def ensure_sandbox(self, sandbox_id: str, *, env: dict[str, str]):
            calls.append(("ensure_sandbox", (sandbox_id, env)))
            return SimpleNamespace(id=sandbox_id)

        async def get_app_access_url(
            self,
            sandbox_id: str,
            app_name: str,
            *,
            ttl_seconds: int,
        ):
            calls.append(("get_app_access_url", (sandbox_id, app_name, ttl_seconds)))
            return SimpleNamespace(
                app="browser",
                url="https://browser.example/access-token",
                expires_at=1893456000,
            )

        async def close(self):
            calls.append(("close", None))

    monkeypatch.setattr(
        user_interaction_adapter,
        "AgentBoxClient",
        FakeAgentBoxClient,
    )
    monkeypatch.setattr(
        user_interaction_adapter.WorkspaceSandboxService,
        "_resolve_runtime",
        lambda: "docker",
    )
    monkeypatch.setattr(
        user_interaction_adapter.WorkspaceSandboxService,
        "resolve_workspace_host_url_for_runtime",
        lambda runtime, api_url: f"{runtime}:{api_url}",
    )
    monkeypatch.setattr(user_interaction_adapter.settings, "agentbox_api_url", "https://agentbox.test")
    monkeypatch.setattr(user_interaction_adapter.settings, "agentbox_api_key", "agentbox-key")
    monkeypatch.setattr(user_interaction_adapter.settings, "api_url", "https://api.test")

    ctx = SimpleNamespace(deps=SimpleNamespace(user_id=user_id))

    response = await display_resource(
        ctx,  # type: ignore[arg-type]
        DisplayResourceRequest(type="browser"),  # type: ignore[arg-type]
    )

    assert response.success is True
    assert response.message == "BROWSER resource ready for display."
    assert response.app == "browser"
    assert response.url == "https://browser.example/access-token"
    assert response.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert calls == [
        ("init", ("https://agentbox.test", "agentbox-key", 300.0)),
        (
            "ensure_sandbox",
            (
                user_id.hex,
                {"LEMMA_BASE_URL": "docker:https://api.test"},
            ),
        ),
        ("get_app_access_url", (user_id.hex, "browser", 1800)),
        ("close", None),
    ]


def _payload_error(**kwargs) -> str:
    """Build a request and return its semantic-validation error (or '')."""
    return validate_display_payload(DisplayResourceRequest(**kwargs)) or ""


def test_display_resource_validates_widget_form_and_table_payloads():
    # Enum coercion + field-level coercions still happen at construction time and
    # never raise.
    browser = DisplayResourceRequest(type="browser")  # type: ignore[arg-type]
    assert browser.type == DisplayResourceType.BROWSER

    # Semantic payload checks moved OUT of a raising pydantic validator into a
    # plain function the tool body calls, so a bad payload becomes a
    # success:false/error result instead of a construction-time ValueError. The
    # function returns the error message, or None when the payload is valid.
    assert "only accept type" in _payload_error(
        type=DisplayResourceType.BROWSER, name="anything"
    )
    assert "exactly one" in _payload_error(
        type=DisplayResourceType.WIDGET, name="chart"
    )

    # A WIDGET with exactly one of public_url/content is valid (construction no
    # longer raises; validation is the function returning None).
    assert (
        validate_display_payload(
            DisplayResourceRequest(
                type=DisplayResourceType.WIDGET, content="<div>chart</div>"
            )
        )
        is None
    )
    assert (
        validate_display_payload(
            DisplayResourceRequest(
                type=DisplayResourceType.WIDGET,
                public_url="https://example.com/widget",
            )
        )
        is None
    )

    # path is FILE-only now; WIDGET no longer accepts it.
    assert "path is only valid for FILE" in _payload_error(
        type=DisplayResourceType.WIDGET, path="/pod/widget.html"
    )
    # Providing both widget payloads is rejected.
    assert "exactly one of public_url or content" in _payload_error(
        type=DisplayResourceType.WIDGET,
        public_url="https://example.com/widget",
        content="<div>chart</div>",
    )

    # FORM has been removed: the enum no longer carries it, and structured input
    # is collected via ask_user (choices) or an interactive WIDGET.
    assert not hasattr(DisplayResourceType, "FORM")
    assert "json_schema" not in DisplayResourceRequest.model_fields

    table = DisplayResourceRequest(
        type=DisplayResourceType.TABLE,
        name="expenses",
        filters=[{"field": "status", "op": "eq", "value": "OPEN"}],
    )
    assert table.name == "expenses"
    assert table.filters is not None
    assert table.filters[0].field == "status"
    assert validate_display_payload(table) is None

    all_agents = DisplayResourceRequest(type=DisplayResourceType.AGENT)
    assert all_agents.name is None
    assert validate_display_payload(all_agents) is None

    lowercase_agent_type = DisplayResourceRequest(type="agent")  # type: ignore[arg-type]
    assert lowercase_agent_type.type == DisplayResourceType.AGENT

    assert "path is only valid" in _payload_error(
        type=DisplayResourceType.AGENT, name="researcher", path="/me/not-for-agent"
    )
    assert "only valid for TABLE" in _payload_error(
        type=DisplayResourceType.AGENT, name="researcher", query="SELECT 1"
    )
    assert "filters require name" in _payload_error(
        type=DisplayResourceType.TABLE,
        filters=[{"field": "status", "op": "eq", "value": "OPEN"}],
    )


@pytest.mark.asyncio
async def test_display_resource_invalid_payload_returns_success_false():
    """An invalid payload comes back as a uniform success:false/error result."""
    ctx = SimpleNamespace(
        deps=SimpleNamespace(surface_platform=None, conversation_id=uuid4()),
        tool_call_id="tc",
    )
    response = await display_resource(
        ctx, DisplayResourceRequest(type=DisplayResourceType.WIDGET, name="chart")
    )
    assert response.success is False
    assert "exactly one" in (response.error or "")


def _approval_ctx(approval_id: str, *, supports_pause_signal: bool = True):
    return SimpleNamespace(
        deps=SimpleNamespace(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            supports_pause_signal=supports_pause_signal,
        ),
        tool_call_id=approval_id,
    )


@pytest.mark.asyncio
async def test_request_approval_pauses_the_run():
    """request_approval raises the pause signal instead of blocking the worker.

    Execution as the user and the denial path now happen on resume in the
    conversation service; the tool's only job is to end the run cleanly.
    """
    approval_id = "approval-tool-call"
    ctx = _approval_ctx(approval_id)

    with pytest.raises(AgentInputRequired) as excinfo:
        await request_approval(
            ctx,  # type: ignore[arg-type]
            tool_name="exec_command",
            args={"cmd": "lemma records delete orders --id 42"},
            title="Delete order 42?",
            reason="Cleaning up a duplicate order.",
        )
    assert excinfo.value.tool_call_id == approval_id
    assert excinfo.value.kind == "request_approval"


@pytest.mark.asyncio
async def test_interaction_tools_guide_instead_of_pausing_on_daemon_harness():
    """On daemon/MCP runs (no pause signal) the tools never raise or block; they
    return guidance so the model falls back to a conversational ask."""
    ask = await ask_user(
        _ask_ctx(supports_pause_signal=False),  # type: ignore[arg-type]
        _one_question(),
    )
    assert ask.success is False
    assert "continue this conversation" in (ask.message or "")

    approval = await request_approval(
        _approval_ctx("approval-daemon", supports_pause_signal=False),  # type: ignore[arg-type]
        tool_name="exec_command",
        args={"cmd": "ls"},
        title="List files?",
    )
    assert approval.success is False
    assert "can't run a tool with the user's approval" in (approval.message or "")


@pytest.mark.asyncio
async def test_request_approval_rejects_bad_input_without_pausing():
    """Invalid input returns success:false WITHOUT raising the pause signal."""
    ctx = SimpleNamespace(
        deps=SimpleNamespace(conversation_id=uuid4(), agent_run_id=uuid4()),
        tool_call_id="approval-self",
    )
    self_approval = await request_approval(
        ctx,  # type: ignore[arg-type]
        tool_name="request_approval",
        args={},
        title="Recursive?",
    )
    assert self_approval.success is False
    assert "cannot approve itself" in (self_approval.error or "")

    no_run = await request_approval(
        SimpleNamespace(  # type: ignore[arg-type]
            deps=SimpleNamespace(conversation_id=uuid4(), agent_run_id=None),
            tool_call_id="approval-x",
        ),
        tool_name="exec_command",
        args={"cmd": "ls"},
        title="List?",
    )
    assert no_run.success is False
    assert "active agent run" in (no_run.error or "")


def test_user_interaction_toolset_includes_ask_user():
    # ask_user ships inside the user_interaction toolset, so it reaches the pod
    # default assistant (which has USER_INTERACTION) and any agent that selected it.
    assert "ask_user" in user_interaction_toolset.tools
    assert "display_resource" in user_interaction_toolset.tools
    assert "request_approval" in user_interaction_toolset.tools


def _ask_ctx(*, supports_pause_signal: bool = True):
    return SimpleNamespace(
        deps=SimpleNamespace(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            supports_pause_signal=supports_pause_signal,
        ),
        tool_call_id="question-call",
    )


def _one_question(**overrides) -> AskUserRequest:
    question = {
        "question": "Which auth method should we use?",
        "header": "Auth",
        "options": [
            {"label": "OAuth", "description": "Use OAuth", "recommended": True},
            {"label": "API key", "description": "Use an API key"},
        ],
    }
    question.update(overrides)
    return AskUserRequest(questions=[question])


@pytest.mark.asyncio
async def test_ask_user_pauses_the_run():
    """ask_user raises the pause signal; answers are replayed on resume."""
    response_or_raise = pytest.raises(AgentInputRequired)
    with response_or_raise as excinfo:
        await ask_user(_ask_ctx(), _one_question())  # type: ignore[arg-type]
    assert excinfo.value.tool_call_id == "question-call"
    assert excinfo.value.kind == "ask_user"


@pytest.mark.asyncio
async def test_ask_user_rejects_bad_input_without_pausing():
    """Invalid input returns success:false WITHOUT ever raising the pause signal."""
    no_questions = await ask_user(  # type: ignore[arg-type]
        _ask_ctx(), AskUserRequest(questions=[])
    )
    assert no_questions.success is False
    assert "at least one question" in (no_questions.error or "")

    one_option = await ask_user(  # type: ignore[arg-type]
        _ask_ctx(),
        _one_question(options=[{"label": "Only", "description": "the sole choice"}]),
    )
    assert one_option.success is False
    assert "2 and 4 options" in (one_option.error or "")


def test_runtime_context_brief_is_appended_to_agent_prompt():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="pod_assistant",
        instruction="Answer briefly.",
    )
    ctx = SimpleNamespace(
        context_brief="# Runtime Context\n- Pod: Acme (abc)\n- User: a@b.co (123)"
    )

    prompt = build_agent_instructions(agent=agent, conversation=conversation, ctx=ctx)

    assert "# Runtime Context" in prompt
    assert "Pod: Acme (abc)" in prompt
    # Always appended after the agent instructions.
    assert prompt.index("Answer briefly.") < prompt.index("# Runtime Context")


def test_daemon_prompt_includes_surface_platform_fragment():
    # Daemon harnesses (include_toolset_prompts=True) get per-platform guidance
    # appended in build_agent_instructions; the in-process harness gets it from
    # SurfacePlatformCapability instead.
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="responder",
        instruction="Answer briefly.",
    )

    on_slack = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(surface_platform="SLACK"),
    )
    assert "Talking over Slack" in on_slack
    assert "Channel background context" in on_slack

    off_surface = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(surface_platform=None),
    )
    assert "Talking over" not in off_surface


def test_workspace_agent_prompt_states_working_directory():
    # A user agent with WORKSPACE_CLI is told its cwd, to work there (not /tmp),
    # and to deliver artifacts to /me.
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="builder",
        instruction="Do the task.",
        toolsets=[AgentToolset.WORKSPACE_CLI],
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(
            workspace_cwd="/workspace/conversations/abc", surface_platform=None
        ),
    )
    assert "# Working Directory" in prompt
    assert "/workspace/conversations/abc" in prompt
    assert "/tmp" in prompt  # warns against scratch dirs
    assert "/me/" in prompt  # artifact delivery guidance
    assert "pip install" in prompt  # on-demand package guidance
    # The non-root sandbox can't write the system env, so steer away from uv --system.
    assert "uv pip install --system" not in prompt


def test_workspace_directory_falls_back_to_conversation_path():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="builder",
        instruction="",
        toolsets=[AgentToolset.WORKSPACE_CLI],
    )

    prompt = build_agent_instructions(
        agent=agent, conversation=conversation, ctx=SimpleNamespace()
    )
    assert f"/workspace/conversations/{conversation.id}" in prompt


def test_pod_assistant_prompt_states_working_directory():
    # The pod-default assistant (agent_id None) has the full toolset incl.
    # WORKSPACE_CLI, so it also gets the working-directory guidance.
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="assistant",
        instruction="",
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(workspace_cwd="/workspace/conversations/xyz"),
    )
    assert "# Working Directory" in prompt
    assert "/workspace/conversations/xyz" in prompt


def test_non_workspace_agent_prompt_omits_working_directory():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="chatter",
        instruction="Just chat.",
        toolsets=[],
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(workspace_cwd="/workspace/conversations/abc"),
    )
    assert "# Working Directory" not in prompt


def test_connector_access_modes_use_account_ownership_names():
    user_owned = ConnectorAccessConfig(app_name="gmail", mode="DYNAMIC")
    agent_owned_account_id = uuid4()
    agent_owned = ConnectorAccessConfig(
        app_name="gmail",
        mode="AGENT_OWNED",
        account_id=agent_owned_account_id,
    )

    assert user_owned.mode == ConnectorMode.USER_OWNED
    assert user_owned.to_dict() == {"app_name": "gmail", "mode": "USER_OWNED"}
    assert agent_owned.to_dict() == {
        "app_name": "gmail",
        "mode": "AGENT_OWNED",
        "account_id": str(agent_owned_account_id),
    }


def test_callable_function_tool_uses_function_name_prefix():
    function = FunctionEntity(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="normalize_contact",
        type=FunctionType.API,
    )
    parent_agent = Agent(
        id=uuid4(),
        pod_id=function.pod_id,
        user_id=uuid4(),
        name="test_agent",
        instruction="",
    )

    tool = AgentCallableToolFactory(uow_factory=object())._build_function_tool(
        function, parent_agent=parent_agent
    )

    assert tool.name == "function_normalize_contact"
    assert tool.tool_def.name == "function_normalize_contact"


@pytest.mark.asyncio
async def test_callable_function_tool_passes_flat_model_args_as_input(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: the model emits the function's flat input schema, so pydantic-ai
    invokes the tool with top-level kwargs (e.g. ``apps=...``). These must be
    collected into ``input_data`` rather than raising
    ``unexpected keyword argument 'apps'``."""
    from app.modules.function.domain.entities import FunctionRunStatus

    class _FakeUow:
        session = None  # placeholder; AuthorizationDataService is patched in the test

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    captured: dict[str, object] = {}

    class _FakeService:
        async def execute_function(self, *, pod_id, name, input_data, user_id, ctx=None, run_as_workload=None):
            captured["input_data"] = input_data
            captured["user_id"] = user_id
            return SimpleNamespace(
                status=FunctionRunStatus.COMPLETED,
                output_data={"ok": True},
                error=None,
            )

    function = FunctionEntity(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="lookup_apps",
        type=FunctionType.API,
        input_schema={
            "type": "object",
            "properties": {"apps": {"type": "array"}, "query": {"type": "string"}},
        },
    )
    parent_agent = Agent(
        id=uuid4(),
        pod_id=function.pod_id,
        user_id=uuid4(),
        name="test_agent",
        instruction="",
    )

    # The tool delegates to FunctionUseCases.execute_function_as_workload (which
    # owns auth + run creation); patch the use-case builder to capture input_data.
    class _FakeUseCases:
        async def execute_function_as_workload(
            self,
            *,
            pod_id,
            name,
            input_data,
            user_id,
            principal_type,
            principal_id,
            delegation_scope,
            delegation_actor_name,
            run_as_workload=None,
        ):
            captured["input_data"] = input_data
            captured["user_id"] = user_id
            captured["run_as_workload"] = run_as_workload
            captured["principal_type"] = principal_type
            captured["principal_id"] = principal_id
            return SimpleNamespace(
                id=uuid4(),
                status=FunctionRunStatus.COMPLETED,
                output_data={"ok": True},
                error=None,
            )

    monkeypatch.setattr(
        "app.modules.agent.tools.callable_tool_factory.build_function_use_cases",
        lambda uow_factory: _FakeUseCases(),
    )

    factory = AgentCallableToolFactory(uow_factory=lambda: _FakeUow())
    tool = factory._build_function_tool(function, parent_agent=parent_agent)

    user_id = uuid4()
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            user_id=user_id,
            workload_type="agent",
            workload_id=parent_agent.id,
            agent_name=parent_agent.name,
        )
    )
    validated = tool.function_schema.validator.validate_python(
        {"apps": ["gmail", "slack"], "query": "x"}
    )
    result = await tool.function_schema.call(validated, ctx)

    assert result == {"ok": True}
    assert captured["input_data"] == {"apps": ["gmail", "slack"], "query": "x"}
    assert captured["user_id"] == user_id
    # The agent's function.execute grant is enforced via the AGENT principal,
    # but the function itself must run under its OWN identity (run_as_workload
    # unset -> executor defaults to the function principal), same as the
    # direct-user and JOB paths. No parent-grant mirroring.
    assert captured["run_as_workload"] is None
    assert captured["principal_type"] == "AGENT"
    assert captured["principal_id"] == parent_agent.id


def test_callable_agent_tool_uses_agent_name_prefix():
    child_agent = Agent(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="child_agent",
        instruction="Answer briefly.",
    )
    parent_agent = Agent(
        id=uuid4(),
        pod_id=child_agent.pod_id,
        user_id=uuid4(),
        name="parent_agent",
        instruction="",
    )

    tool = AgentCallableToolFactory(uow_factory=object())._build_agent_tool(
        child_agent, parent_agent=parent_agent
    )

    assert tool.name == "agent_child_agent"
    assert tool.tool_def.name == "agent_child_agent"


def _agent_pair(*, input_schema=None, output_schema=None):
    child = Agent(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="child_agent",
        instruction="Answer briefly.",
        input_schema=input_schema,
        output_schema=output_schema,
    )
    parent = Agent(
        id=uuid4(),
        pod_id=child.pod_id,
        user_id=uuid4(),
        name="parent_agent",
        instruction="",
    )
    return child, parent


def _patch_subagent(monkeypatch, *, await_result, captured=None):
    import app.modules.agent.services.subagent_service as sub_mod

    class _FakeSub:
        def __init__(self, uow_factory):
            del uow_factory

        async def spawn(self, deps, *, agent_name, input_data):
            if captured is not None:
                captured["input_data"] = input_data
                captured["agent_name"] = agent_name
            return SimpleNamespace(conversation_id=uuid4(), run_id=uuid4())

        async def await_run(self, deps, *, conversation_id, run_id, timeout_seconds):
            return await_result

    monkeypatch.setattr(sub_mod, "SubAgentService", _FakeSub)


def _agent_tool(child, parent):
    return AgentCallableToolFactory(uow_factory=object())._build_agent_tool(
        child, parent_agent=parent
    )


def test_agent_tool_single_string_input_when_no_input_schema():
    child, parent = _agent_pair()
    schema = _agent_tool(child, parent).function_schema.json_schema
    assert set(schema["properties"]) == {"input"}
    assert schema["properties"]["input"]["type"] == "string"
    assert schema["required"] == ["input"]
    assert schema.get("additionalProperties") is False


def test_agent_tool_structured_input_when_input_schema_set():
    child, parent = _agent_pair(
        input_schema={"type": "object", "properties": {"topic": {"type": "string"}}}
    )
    schema = _agent_tool(child, parent).function_schema.json_schema
    assert "topic" in schema["properties"]
    assert "input" not in schema["properties"]


@pytest.mark.asyncio
async def test_agent_tool_returns_string_when_no_output_schema(monkeypatch):
    captured: dict = {}
    # A no-schema run stores its final text as {"answer": <text>} — the tool must
    # unwrap it to a plain string for the parent model.
    _patch_subagent(
        monkeypatch,
        await_result={"output": {"answer": "the answer"}, "status": "COMPLETED"},
        captured=captured,
    )
    child, parent = _agent_pair()
    tool = _agent_tool(child, parent)
    ctx = SimpleNamespace(deps=SimpleNamespace(agent_name="parent_agent"))
    validated = tool.function_schema.validator.validate_python({"input": "do it"})
    result = await tool.function_schema.call(validated, ctx)
    assert result == "the answer"
    assert captured["input_data"] == {"input": "do it"}


@pytest.mark.asyncio
async def test_agent_tool_no_output_schema_failed_returns_error_string(monkeypatch):
    _patch_subagent(
        monkeypatch,
        await_result={"output": None, "status": "FAILED", "error": "boom"},
    )
    child, parent = _agent_pair()
    tool = _agent_tool(child, parent)
    ctx = SimpleNamespace(deps=SimpleNamespace(agent_name="parent_agent"))
    validated = tool.function_schema.validator.validate_python({"input": "x"})
    result = await tool.function_schema.call(validated, ctx)
    assert result == "boom"


@pytest.mark.asyncio
async def test_agent_tool_returns_dict_when_output_schema_set(monkeypatch):
    _patch_subagent(
        monkeypatch,
        await_result={"output": {"k": "v"}, "status": "COMPLETED"},
    )
    child, parent = _agent_pair(
        output_schema={"type": "object", "properties": {"k": {"type": "string"}}}
    )
    tool = _agent_tool(child, parent)
    ctx = SimpleNamespace(deps=SimpleNamespace(agent_name="parent_agent"))
    validated = tool.function_schema.validator.validate_python({})
    result = await tool.function_schema.call(validated, ctx)
    assert result == {"k": "v"}


@pytest.mark.asyncio
async def test_agent_tool_timeout_returns_handle_dict_even_no_output_schema(monkeypatch):
    _patch_subagent(
        monkeypatch,
        await_result={"timed_out": True, "status": "RUNNING"},
    )
    child, parent = _agent_pair()
    tool = _agent_tool(child, parent)
    ctx = SimpleNamespace(deps=SimpleNamespace(agent_name="parent_agent"))
    validated = tool.function_schema.validator.validate_python({"input": "x"})
    result = await tool.function_schema.call(validated, ctx)
    assert isinstance(result, dict)
    assert "conversation_id" in result and "run_id" in result


@pytest.mark.asyncio
async def test_sub_agent_conversation_excludes_subagents_toolset():
    # Depth=1: a run that IS a spawned sub-agent (metadata is_sub_agent=True) must
    # not get the sub-agent control toolset. The source of truth is the metadata
    # flag, not parent_id.
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    agent = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="orchestrator",
        instruction="",
        toolsets=[AgentToolset.SUBAGENTS, AgentToolset.WEB_SEARCH],
    )
    top = Conversation(pod_id=agent.pod_id, user_id=agent.user_id, agent_id=agent.id)
    sub_agent = Conversation(
        pod_id=agent.pod_id,
        user_id=agent.user_id,
        agent_id=agent.id,
        parent_id=uuid4(),
        metadata={"is_sub_agent": True},
    )

    top_ts = await runner.tool_assembler.assemble(agent=agent, conversation=top)
    sub_ts = await runner.tool_assembler.assemble(agent=agent, conversation=sub_agent)

    assert subagents_toolset in top_ts
    assert subagents_toolset not in sub_ts
    assert web_search_toolset in sub_ts  # non-spawn tools survive


@pytest.mark.asyncio
async def test_project_child_conversation_keeps_subagents_toolset():
    # A conversation pinned under a PROJECT has a parent_id but is NOT a sub-agent,
    # so it keeps its full spawning ability (parent_id alone must not gate).
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    agent = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="orchestrator",
        instruction="",
        toolsets=[AgentToolset.SUBAGENTS, AgentToolset.WEB_SEARCH],
    )
    project_child = Conversation(
        pod_id=agent.pod_id,
        user_id=agent.user_id,
        agent_id=agent.id,
        parent_id=uuid4(),  # pinned under a project, but not spawned as a sub-agent
        metadata={"cwd": "/workspace/projects/foo"},
    )

    child_ts = await runner.tool_assembler.assemble(
        agent=agent, conversation=project_child
    )

    assert subagents_toolset in child_ts


def _conversation_service_with_repo(repo):
    from app.modules.agent.services.conversation_service import ConversationService

    return ConversationService(
        uow=None,
        conversation_repository=repo,
        agent_repository=None,
        authorization_service=None,
    )


@pytest.mark.asyncio
async def test_child_conversation_inherits_parent_cwd_and_workspace():
    # A child (parent_id set) shares the parent's resolved cwd + workspace
    # selection instead of getting its own directory.
    parent_id = uuid4()
    parent = Conversation(
        id=parent_id,
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"cwd": "/workspace/projects/alpha", "workspace_id": "ws-1"},
    )

    class _Repo:
        async def get_conversation(self, conversation_id, *args, **kwargs):
            return parent if conversation_id == parent_id else None

    service = _conversation_service_with_repo(_Repo())
    child = Conversation(
        pod_id=parent.pod_id, user_id=parent.user_id, parent_id=parent_id
    )
    await service._apply_inherited_cwd(child, parent_id=parent_id)

    assert child.metadata["cwd"] == "/workspace/projects/alpha"
    assert child.metadata["workspace_id"] == "ws-1"


@pytest.mark.asyncio
async def test_root_conversation_gets_own_cwd():
    class _Repo:
        async def get_conversation(self, *args, **kwargs):
            return None

    service = _conversation_service_with_repo(_Repo())
    convo = Conversation(pod_id=uuid4(), user_id=uuid4())
    await service._apply_inherited_cwd(convo, parent_id=None)

    # A root gets its own pretty c/{date}/{slug} cwd stamped into metadata.
    date = convo.created_at.date().isoformat()
    cwd = convo.metadata["cwd"]
    assert cwd.startswith(f"/workspace/c/{date}/")
    assert cwd.count("/") == 4  # /workspace/c/{date}/{slug}


@pytest.mark.asyncio
async def test_explicit_cwd_in_metadata_is_not_overridden():
    class _Repo:
        async def get_conversation(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("parent should not be fetched when cwd is explicit")

    service = _conversation_service_with_repo(_Repo())
    convo = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        parent_id=uuid4(),
        metadata={"cwd": "/workspace/custom"},
    )
    await service._apply_inherited_cwd(convo, parent_id=convo.parent_id)

    assert convo.metadata["cwd"] == "/workspace/custom"


def test_runner_uses_final_answer_tool_for_structured_output_agents():
    agent = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="structured_agent",
        instruction="Return structured output",
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )

    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    conversation = Conversation(
        pod_id=agent.pod_id,
        user_id=agent.user_id,
        agent_id=agent.id,
        type=ConversationType.TASK,
    )

    assert runner._resolve_output_type(agent, conversation).__name__ == "final_answer"


def test_final_answer_tool_output_becomes_normal_assistant_message():
    message = PydanticAIHarness()._final_output_message(
        output=FinalAgentResult(
            status="COMPLETED",
            output={"answer": "Done brother", "score": 1},
        ),
        tool_name="final_result_final_answer",
        tool_call_id="call-final",
    )

    assert message is not None
    assert message.role == MessageRole.ASSISTANT
    assert message.kind == MessageKind.TEXT
    assert message.text == "Done brother"
    assert message.metadata["is_final_answer"] is True
    assert message.metadata["structured_output"]["score"] == 1


def test_waiting_final_answer_tool_output_sets_waiting_metadata():
    message = PydanticAIHarness()._final_output_message(
        output=FinalAgentResult(
            status="WAITING",
            output="What customer id should I use?",
        ),
        tool_name="final_result_final_answer",
        tool_call_id="call-final",
    )

    assert message is not None
    assert message.role == MessageRole.ASSISTANT
    assert message.text == "What customer id should I use?"
    assert message.metadata["final_answer_status"] == "WAITING"
    assert message.metadata["structured_output"] == "What customer id should I use?"


def test_runner_keeps_last_five_agent_runs_in_full_and_elides_older_runs():
    runs = [
        _agent_run_with_messages(run_index)
        for run_index in range(FULL_HISTORY_AGENT_RUN_COUNT + 1)
    ]
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())

    selected = runner._select_runtime_history(runs)
    grouped = _messages_by_run(selected)
    older_run = runs[0]

    assert len(grouped[older_run.id]) == 3
    assert grouped[older_run.id][1].metadata["summary_kind"] == (
        "agent_run_middle_elision"
    )
    assert grouped[older_run.id][1].metadata["elided_message_count"] == 3

    for recent_run in runs[-FULL_HISTORY_AGENT_RUN_COUNT:]:
        assert len(grouped[recent_run.id]) == 5


def test_history_processors_add_100k_summarizer_by_default():
    processors = build_history_processors(
        HarnessOptions(model_name="kimi-k2.6"),
        summarization_model="openai:gpt-4.1",
    )

    assert len(processors) == 1
    assert processors[0].trigger == ("tokens", 100_000)
    assert processors[0].keep == ("messages", 20)


def test_history_processors_can_disable_default_summarizer():
    processors = build_history_processors(
        HarnessOptions(
            model_name="kimi-k2.6",
            history_summarization_enabled=False,
        ),
        summarization_model="openai:gpt-4.1",
    )

    assert processors == []


def test_conversation_instructions_are_appended_to_agent_prompt():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        instructions="Use the task board screen as the current UI context.",
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="pod_assistant",
        instruction="Answer briefly.",
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=object(),
    )

    # Base prompt skill guidance is present...
    assert "Do not load a skill for ordinary CLI usage" in prompt
    # ...and the conversation instructions are appended under their own section.
    assert "# Conversation Instructions" in prompt
    assert "Use the task board screen as the current UI context." in prompt
    # Skill catalog guidance for the builder/user skills is present.
    assert "lemma-builder" in prompt
    assert "lemma-user" in prompt
    assert "private code sandbox" in prompt
    assert "/me/<descriptive-folder>/" in prompt
    assert "lemma files cat /pod/knowledge/policy.pdf --pages 3-7" in prompt
    assert 'lit parse input.pdf --target-pages "1-5,10"' in prompt
    assert "# Agent Instructions\nAnswer briefly." in prompt
    assert (
        "# Conversation Instructions\nUse the task board screen as the current UI context."
        in prompt
    )


def test_default_pod_assistant_prompt_uses_base_file_without_extra_instruction():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="pod_assistant",
        instruction="",
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=object(),
    )

    assert prompt.startswith("You are the assistant for this Lemma pod")
    assert "Structure for state, prose for knowledge" in prompt
    assert "## Web Search" in prompt
    assert 'lemma tools web-search "query terms" --limit 5' in prompt
    assert "# Agent Instructions" not in prompt
    assert "# Conversation Instructions" not in prompt


def test_persisted_agent_prompt_omits_web_search_without_toolset():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="researchless_agent",
        instruction="Answer from pod data only.",
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=object(),
    )

    assert "## Web Search" not in prompt
    assert "lemma tools web-search" not in prompt


def test_persisted_agent_prompt_includes_web_search_with_toolset():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="research_agent",
        instruction="Research current topics.",
        toolsets=[AgentToolset.WEB_SEARCH],
    )

    prompt = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=object(),
    )

    assert "## Web Search" in prompt
    assert "save-webpage https://example.com/article" in prompt


def test_daemon_instructions_include_todo_guidance_only_with_toolset():
    # Daemon harnesses get toolset prompts folded into instructions (no capability
    # layer). The todo task-list guidance must ride along — but only when the agent
    # actually has TODO — so daemons behave like the in-process LEMMA harness.
    pod_id, user_id = uuid4(), uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=user_id, agent_id=uuid4())

    without_todo = Agent(
        pod_id=pod_id, user_id=user_id, name="plain", instruction="x", toolsets=[]
    )
    assert "# Task list" not in build_agent_instructions(
        agent=without_todo, conversation=conversation, ctx=object()
    )

    with_todo = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="planner",
        instruction="x",
        toolsets=[AgentToolset.TODO],
    )
    daemon_prompt = build_agent_instructions(
        agent=with_todo, conversation=conversation, ctx=object()
    )
    assert "# Task list" in daemon_prompt and "write_todos" in daemon_prompt

    # The in-process LEMMA harness suppresses toolset prompts (the TodoCapability
    # supplies the same guidance), so it's not double-included here.
    lemma_prompt = build_agent_instructions(
        agent=with_todo,
        conversation=conversation,
        ctx=object(),
        include_toolset_prompts=False,
    )
    assert "# Task list" not in lemma_prompt


def test_pod_default_assistant_uses_rich_base_and_all_fragments():
    # The pod-default assistant (no agent_id) gets the rich base plus every toolset
    # fragment, because it runs the full batteries-included toolset.
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="pod_assistant",
        instruction="",
    )

    prompt = build_agent_instructions(
        agent=agent, conversation=conversation, ctx=object()
    )

    assert prompt.startswith("You are the assistant for this Lemma pod")
    assert "## Lemma CLI" in prompt
    assert "## Skills" in prompt
    assert "## Web Search" in prompt
    assert "# Task list" in prompt


def test_user_agent_uses_lean_base_and_only_its_toolset_fragments():
    # A user-created agent gets the lean base + only the fragments for the toolsets
    # it actually has — no full CLI/skills/web dump.
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="cli_only",
        instruction="Work only with pod data.",
        toolsets=[AgentToolset.WORKSPACE_CLI],
    )

    prompt = build_agent_instructions(
        agent=agent, conversation=conversation, ctx=object()
    )

    assert prompt.startswith("You are a Lemma agent")
    assert "You are a Lemma pod assistant" not in prompt
    assert "## Lemma CLI" in prompt  # its one toolset's fragment
    assert "## Web Search" not in prompt
    assert "## Skills" not in prompt
    assert "# Task list" not in prompt
    assert "# Agent Instructions\nWork only with pod data." in prompt


def test_user_agent_without_toolsets_has_no_tool_fragments():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="bare",
        instruction="Answer succinctly.",
        toolsets=[],
    )

    prompt = build_agent_instructions(
        agent=agent, conversation=conversation, ctx=object()
    )

    assert prompt.startswith("You are a Lemma agent")
    for fragment_marker in ("## Lemma CLI", "## Web Search", "## Skills", "# Task list"):
        assert fragment_marker not in prompt


def test_latest_user_prompt_renders_channel_context_as_background():
    conversation_id = uuid4()
    message = Message(
        conversation_id=conversation_id,
        sequence=0,
        role=MessageRole.USER.value,
        kind=MessageKind.TEXT,
        text="what happened?",
        metadata={
            "surface_platform": "SLACK",
            "sender_display_name": "Anukul",
            "channel_context": [
                {"author": "U-ALICE", "text": "Can someone summarize the incident?"},
                {"author": "U-BOB", "text": "It started at 2pm."},
            ],
        },
    )

    _history, user_prompt = PydanticAIHarness()._history_and_prompt([message])

    assert user_prompt is not None
    # The current message and a clearly-framed background-context block are present.
    assert "what happened?" in user_prompt
    assert "BACKGROUND CONTEXT" in user_prompt
    assert "Can someone summarize the incident?" in user_prompt
    assert "It started at 2pm." in user_prompt
    assert "NOT" in user_prompt  # framed as not-instructions
    # The stored message text is untouched.
    assert message.text == "what happened?"


def test_latest_user_prompt_includes_metadata_state_without_changing_content():
    conversation_id = uuid4()
    message = Message(
        conversation_id=conversation_id,
        sequence=0,
        role=MessageRole.USER.value,
        kind=MessageKind.TEXT,
        text="What should I do next?",
        metadata={
            "state": {
                "screen": "pod_runs",
                "selected_run_id": "run-123",
            }
        },
    )

    history, user_prompt = PydanticAIHarness()._history_and_prompt([message])

    assert history == []
    assert user_prompt is not None
    assert user_prompt.startswith("What should I do next?")
    assert "UI state:" in user_prompt
    assert '"screen": "pod_runs"' in user_prompt
    assert message.text == "What should I do next?"
