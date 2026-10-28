"""Every tool surface, byte for byte, against a recorded golden.

What a model or an MCP client is shown -- each tool's name, description, JSON
schema and annotations, the instructions the tools bring, and which tools sit
behind tool search -- is a contract. This dumps every surface for a spread of
runs and compares it with ``goldens/tool_surfaces.json``:

* the in-process harness, as the model's first request sees it;
* the Agent Host conversation MCP listing;
* the pod MCP listing, for a Lemma session and for a read-only outside client.

Regenerate only for an intended change: ``UPDATE_TOOL_GOLDENS=1 uv run pytest
app/modules/agent/tests/unit/test_tool_surface_golden.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.core.auth_state_cache import AccountStanding
from app.core.security import PrincipalLookups
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.calls import CallMark, CallRole
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.value_objects import (
    AgentToolset,
    ConversationOrigin,
    ConversationType,
)
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    harness_capabilities,
)
from app.modules.agent.infrastructure.harness.lemma.run_options import (
    final_answer_output,
)
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.agent_host.mcp import (
    ConversationMCPService,
)
from app.modules.agent.services.pod_mcp_service import (
    PodMCPService,
    VerifiedSession,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.toolsets import AgentGrantSummary
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for
from app.modules.agent.services.tool_runtime import ToolRuntime
from app.modules.mcp_access.contracts import McpPrincipal, Scope
from app.modules.workspace.contracts.host_execution import HostWorkspace

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "goldens" / "tool_surfaces.json"

USER = UUID("00000000-0000-4000-8000-000000000001")
POD = UUID("00000000-0000-4000-8000-000000000002")
ORG = UUID("00000000-0000-4000-8000-000000000003")
CONVERSATION = UUID("00000000-0000-4000-8000-000000000004")
RUN = UUID("00000000-0000-4000-8000-000000000005")
NAMED_AGENT = UUID("00000000-0000-4000-8000-000000000006")
CHILD_AGENT = UUID("00000000-0000-4000-8000-000000000007")
FUNCTION = UUID("00000000-0000-4000-8000-000000000008")
CONTACT = UUID("00000000-0000-4000-8000-000000000009")
SANDBOX = UUID("00000000-0000-4000-8000-00000000000a")


class _UoW:
    session = None

    async def __aenter__(self) -> _UoW:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def commit(self) -> None:
        return None


def _uow_factory() -> _UoW:
    return _UoW()


# -- Scenarios ----------------------------------------------------------------


def _pod_default() -> Agent:
    return Agent(
        id=UUID("00000000-0000-4000-8000-0000000000aa"),
        pod_id=POD,
        user_id=USER,
        name="lem",
        kind=AgentKind.POD_DEFAULT,
        instruction="You are the pod's assistant.",
    )


def _named(**fields: Any) -> Agent:
    return Agent(
        id=NAMED_AGENT,
        pod_id=POD,
        user_id=USER,
        name="researcher",
        instruction="Research things.",
        **fields,
    )


def _conversation(**fields: Any) -> Conversation:
    return Conversation(
        id=CONVERSATION,
        user_id=USER,
        pod_id=POD,
        organization_id=ORG,
        **fields,
    )


def _ctx(agent: Agent, conversation: Conversation, **fields: Any) -> ToolContext:
    return ToolContext(
        user_id=USER,
        org_id=ORG,
        pod_id=POD,
        conversation_id=conversation.id,
        agent_name=agent.name,
        workload_id=agent.id,
        is_pod_default_agent=agent.kind is AgentKind.POD_DEFAULT,
        audience=Audience.of(conversation),
        **fields,
    )


_NAMED_TOOLSETS = [
    AgentToolset.WORKSPACE_CLI,
    AgentToolset.WEB_SEARCH,
    AgentToolset.BROWSER,
    AgentToolset.SUBAGENTS,
    AgentToolset.SPEECH,
    AgentToolset.MEMORY,
]
_GRANTS = AgentGrantSummary(
    function_ids=(str(FUNCTION),),
    agent_ids=(str(CHILD_AGENT),),
    has_pod_data=True,
    has_connectors=True,
)


def _scenarios() -> dict[str, dict[str, Any]]:
    pod_default = _pod_default()
    named = _named(toolsets=_NAMED_TOOLSETS)
    chat = _conversation()
    task = _conversation(type=ConversationType.TASK)
    slack_task = _conversation(type=ConversationType.TASK)
    contact = _conversation(contact_id=CONTACT)
    call = _conversation(
        call=CallMark(call_id=CONVERSATION, role=CallRole.COORDINATOR, live=True)
    )
    child = _conversation(origin=ConversationOrigin.SUBAGENT)
    host = HostWorkspace(sandbox_id=SANDBOX, root="/Users/someone/project")
    return {
        "pod_default_chat": {
            "agent": pod_default,
            "conversation": chat,
            "ctx": _ctx(
                pod_default,
                chat,
                vision_mode=AgentVisionMode.DIRECT,
                memory_enabled=True,
                open_questions="Somebody asked you something.",
            ),
        },
        "named_agent_chat": {
            "agent": named,
            "conversation": chat,
            "grants": _GRANTS,
            "ctx": _ctx(named, chat, memory_enabled=True, memory_after_turn=True),
        },
        "named_agent_task_on_slack_on_the_mac": {
            "agent": _named(
                toolsets=_NAMED_TOOLSETS,
                output_schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            ),
            "conversation": slack_task,
            "grants": AgentGrantSummary(has_pod_data=True),
            "ctx": _ctx(
                named,
                slack_task,
                surface_platform="SLACK",
                vision_mode=AgentVisionMode.DELEGATED,
                host_workspace=host,
            ),
            "agent_host_ctx": {
                "host_workspace": None,
                "host_runs_native_commands": True,
            },
        },
        "pod_default_task": {
            "agent": pod_default,
            "conversation": task,
            "ctx": _ctx(pod_default, task),
        },
        "contact_on_a_web_page": {
            "agent": pod_default,
            "conversation": contact,
            "ctx": _ctx(pod_default, contact, place_kind="DM", on_web_widget=True),
        },
        "call_coordinator": {
            "agent": pod_default,
            "conversation": call,
            "ctx": _ctx(pod_default, call),
        },
        "sub_agent_child": {
            "agent": named,
            "conversation": child,
            "grants": _GRANTS,
            "ctx": _ctx(named, child),
        },
    }


@pytest.fixture
def _stubbed_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reads a run's tools make while being built, answered without a database."""
    from app.modules.agent.infrastructure.repositories import AgentRepository
    from app.modules.agent.infrastructure.tools.connectors import (
        tools as connectors_adapter,
    )
    from app.modules.agent_surfaces.domain.credentials import SlackCredentials
    from app.modules.agent_surfaces.infrastructure.adapters import (
        platform_tool_factory,
    )
    from app.modules.function.contracts import (
        FunctionEntity,
        FunctionStatus,
        agent_tools,
    )

    async def functions_by_ids(_uow: object, ids: object) -> dict[str, Any]:
        return {
            str(FUNCTION): FunctionEntity(
                id=FUNCTION,
                pod_id=POD,
                user_id=USER,
                name="summarize-report",
                description="Summarize a report.",
                input_schema={
                    "type": "object",
                    "properties": {"report_id": {"type": "string"}},
                    "required": ["report_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                },
                status=FunctionStatus.READY,
            )
        }

    async def agents_by_ids(_self: object, ids: object) -> dict[str, Any]:
        return {
            str(CHILD_AGENT): Agent(
                id=CHILD_AGENT,
                pod_id=POD,
                user_id=USER,
                name="fact-checker",
                description="Checks facts.",
                instruction="Check facts.",
            )
        }

    async def usable_connectors(
        _deps: object,
    ) -> tuple[list[Any], list[Any], dict[Any, str]]:
        auth_config_id = UUID("00000000-0000-4000-8000-0000000000aa")
        connector = SimpleNamespace(
            slug="github",
            connector_id="github",
            mode=SimpleNamespace(value="PER_MEMBER"),
            purpose="Issues and pull requests",
            auth_config_id=auth_config_id,
        )
        offer = SimpleNamespace(name="gmail", connector_id="gmail")
        return [connector], [offer], {auth_config_id: "CONNECTED"}

    monkeypatch.setattr(connectors_adapter, "_usable", usable_connectors)
    monkeypatch.setattr(agent_tools, "get_functions_by_ids", functions_by_ids)
    monkeypatch.setattr(AgentRepository, "get_many", agents_by_ids)
    monkeypatch.setattr(
        platform_tool_factory, "has_native_credentials", lambda _platform: True
    )
    monkeypatch.setattr(
        platform_tool_factory,
        "native_credentials",
        lambda _platform, **_kw: SlackCredentials(),
    )


# -- Dumping ------------------------------------------------------------------


#: Which pydantic-ai capability registered a tool: an implementation detail,
#: and an id that differs from run to run.
_UNSTABLE_DEFINITION_KEYS = frozenset({"capability_id"})


def _tool_definition(tool: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in dataclasses.asdict(tool).items()
        if key not in _UNSTABLE_DEFINITION_KEYS
    }


async def _in_process_listing(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
    grants: AgentGrantSummary | None,
) -> dict[str, Any]:
    """What the model's first request carries, through the real assembly."""
    specs = await specs_for(
        agent=agent,
        conversation=conversation,
        ctx=ctx,
        surface=ToolSurface.IN_PROCESS,
        uow_factory=_uow_factory,
        grants=grants,
    )
    capabilities = harness_capabilities(
        ctx=ctx,
        specs=specs,
        runtime=ToolRuntime(specs),
        enable_prompt_caching=False,
    )
    return await _first_request(
        capabilities=capabilities,
        ctx=ctx,
        output_type=final_answer_output(agent, conversation),
    )


async def _first_request(
    *, capabilities: list[Any], ctx: Any, output_type: Any = None
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def model(_messages: list[Any], info: AgentInfo) -> ModelResponse:
        if not seen:
            parameters = info.model_request_parameters
            seen.update(
                function_tools=[
                    _tool_definition(tool) for tool in parameters.function_tools
                ],
                output_tools=[_tool_definition(tool) for tool in info.output_tools],
                tool_visibility=dict(parameters.tool_visibility or {}),
                instructions=info.instructions,
            )
        if info.output_tools:
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, {"summary": "done"})]
            )
        return ModelResponse(parts=[TextPart("done")])

    pydantic_agent = PydanticAgent(
        FunctionModel(
            model, profile=ModelProfile(tool_deferral_mode="with_tool_search")
        ),
        deps_type=type(ctx),
        output_type=output_type if output_type is not None else str,
        capabilities=capabilities,
    )
    try:
        await pydantic_agent.run("hi", deps=ctx)
    except Exception:  # noqa: BLE001 - only the first request is under test
        pass
    return seen


async def _agent_host_listing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
    grants: AgentGrantSummary | None,
) -> list[dict[str, Any]]:
    service = ConversationMCPService()
    service.uow_factory = _uow_factory

    async def load(**_kwargs: object) -> tuple[Any, ...]:
        return agent, conversation, ctx, grants

    monkeypatch.setattr(service, "_load_agent_context", load)
    tools = await service.list_tools(conversation_id=conversation.id, agent_run_id=RUN)
    return [tool.model_dump(mode="json", exclude_none=True) for tool in tools]


async def _pod_listings() -> dict[str, list[dict[str, Any]]]:
    async def verify(_token: str) -> VerifiedSession:
        return VerifiedSession(user_id=USER, payload={})

    async def standing(_user_id: UUID) -> AccountStanding:
        return AccountStanding(is_active=True, is_verified=True, is_deleted=False)

    async def not_revoked(_actor_id: UUID) -> bool:
        return False

    service = PodMCPService(
        verify_session=verify,
        principal_lookups=PrincipalLookups(
            account_standing=standing, delegation_revoked=not_revoked
        ),
    )
    service.uow_factory = _uow_factory
    session = await service.list_tools(pod_id=POD, token="session")
    read_only = await service.list_tools(
        pod_id=POD,
        token="",
        principal=McpPrincipal(
            user_id=USER,
            pod_id=POD,
            grant_id=UUID(int=11),
            client_id="client",
            client_name="An outside client",
            scopes=frozenset({Scope.READ}),
        ),
    )
    return {
        "lemma_session": [
            t.model_dump(mode="json", exclude_none=True) for t in session
        ],
        "read_only_client": [
            t.model_dump(mode="json", exclude_none=True) for t in read_only
        ],
    }


async def _standalone_toolsets() -> dict[str, Any]:
    """Toolsets no scenario above reaches: the memory pass's, and the surface
    tools that need a surface row to be offered."""
    from pydantic_ai.capabilities import Toolset

    from app.modules.agent.domain.agent_memory_paths import agent_memory_paths
    from app.modules.agent.infrastructure.tools.pod.memory_pass_tools import (
        MemoryScope,
        memory_pass_specs,
    )
    from app.modules.agent_surfaces.domain.credentials import TeamsCredentials
    from app.modules.agent_surfaces.platforms.teams.tools import (
        teams_surface_tools,
    )
    from app.modules.agent_surfaces.services.surface_send_tools import (
        surface_send_tools,
    )
    from app.modules.agent_surfaces.services.whatsapp_group_tool import (
        whatsapp_group_tools,
    )

    named = _named(toolsets=_NAMED_TOOLSETS)
    ctx = _ctx(named, _conversation())
    toolsets = {
        "memory_pass": memory_pass_specs(MemoryScope(agent_memory_paths(named))),
        "teams": teams_surface_tools(credentials=TeamsCredentials()),
        "surface_send": surface_send_tools(),
        "whatsapp_group": whatsapp_group_tools(
            uow_factory=_uow_factory, surface_id=SANDBOX
        ),
    }
    return {
        name: await _first_request(
            capabilities=[Toolset(function_toolset(specs, ToolRuntime(specs)))],
            ctx=ctx,
        )
        for name, specs in toolsets.items()
    }


async def _surfaces(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    in_process: dict[str, Any] = {}
    agent_host: dict[str, Any] = {}
    for name, scenario in _scenarios().items():
        agent, conversation, ctx = (
            scenario["agent"],
            scenario["conversation"],
            scenario["ctx"],
        )
        grants = scenario.get("grants", AgentGrantSummary())
        in_process[name] = await _in_process_listing(
            agent=agent, conversation=conversation, ctx=ctx, grants=grants
        )
        host_ctx = ctx.model_copy(update=scenario.get("agent_host_ctx", {}))
        agent_host[name] = await _agent_host_listing(
            monkeypatch,
            agent=agent,
            conversation=conversation,
            ctx=host_ctx,
            grants=grants,
        )
    return {
        "in_process": in_process,
        "standalone_toolsets": await _standalone_toolsets(),
        "agent_host_mcp": agent_host,
        "pod_mcp": await _pod_listings(),
    }


# -- The golden file ----------------------------------------------------------
#
# Every surface repeats most tool definitions, so the file keeps each distinct
# definition and instruction text once, in ``pool``, and the surfaces name them.


def _key(kind: str, value: object) -> str:
    digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    name = value.get("name") if isinstance(value, dict) else None
    return f"{kind}:{name or ''}@{digest[:12]}"


def _pooled(node: Any, pool: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "name" in node and (
            "parameters_json_schema" in node or "input_schema" in node
        ):
            key = _key("tool", node)
            pool[key] = node
            return key
        pooled = {k: _pooled(v, pool) for k, v in node.items()}
        if isinstance(pooled.get("instructions"), str):
            key = _key("instructions", pooled["instructions"])
            pool[key] = pooled["instructions"]
            pooled["instructions"] = key
        return pooled
    if isinstance(node, list):
        return [_pooled(item, pool) for item in node]
    return node


def _expanded(node: Any, pool: dict[str, Any]) -> Any:
    if isinstance(node, str) and node in pool:
        return pool[node]
    if isinstance(node, dict):
        return {k: _expanded(v, pool) for k, v in node.items()}
    if isinstance(node, list):
        return [_expanded(item, pool) for item in node]
    return node


@pytest.mark.anyio
@pytest.mark.usefixtures("_stubbed_loads")
async def test_every_tool_surface_matches_the_golden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = json.loads(json.dumps(await _surfaces(monkeypatch), default=str))
    if os.environ.get("UPDATE_TOOL_GOLDENS"):
        pool: dict[str, Any] = {}
        surfaces = _pooled(actual, pool)
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(
            json.dumps({"pool": pool, **surfaces}, indent=1, sort_keys=True) + "\n"
        )
    golden = json.loads(GOLDEN.read_text())
    pool = golden.pop("pool")
    assert set(actual) == set(golden)
    for surface, listings in golden.items():
        assert set(actual[surface]) == set(listings), surface
        for name, listing in listings.items():
            assert actual[surface][name] == _expanded(listing, pool), (
                f"{surface}/{name} drifted from the golden"
            )
