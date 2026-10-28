"""Every tool a run answering somebody outside the pod is offered, named one by one.

A toolset is a bundle, and trusting one is how ``web_fetch`` -- which opens the
conversation owner's sandbox -- reached strangers: it rides in WEB_SEARCH beside
``web_search``. So the question is asked of each tool, on its policy
(``ToolPolicy.outsider_allowed``), and these tests hold the line by name:

* the decision record: exactly these tools are allowed, ``web_fetch`` and the
  writes nowhere among them;
* the assembled run: what the model is actually offered is that set;
* the runtime refuses an off-list tool however it is asked for;
* a workspace -- the owner's sandbox, host or files -- is never opened for one.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.outsiders import (
    OUTSIDER_TOOLSETS,
    Audience,
    OutsiderRunRefused,
    refuse_owner_workspace,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    harness_capabilities,
)
from app.modules.agent.infrastructure.tools.contact import contact_specs
from app.modules.agent.infrastructure.tools.form import form_specs
from app.modules.agent.infrastructure.tools.workspace_cli import (
    tools as workspace_cli,
)
from app.modules.agent.infrastructure.tools.workspace_cli.workspace_cli import (
    get_workspace_session,
)
from app.modules.agent.services.tool_catalog import (
    STATIC_SPECS,
    ToolSurface,
    specs_for,
)
from app.modules.agent.services.tool_runtime import WITHHELD_MESSAGE, ToolRuntime

pytestmark = pytest.mark.unit

#: The decision record. Each goes through the anonymous authorizer, reaches no
#: sandbox, and acts as nobody: the pod's reads (what it made Public), a search
#: API call, flagging the channel for a member, a contact's own rows and
#: functions, and filling a form on a web visitor's own page.
ALLOWED = {
    "pod_tables",
    "pod_get_records",
    "pod_query",
    "pod_list_files",
    "pod_read_file",
    "pod_view_document_pages",
    "pod_get_file_url",
    "pod_search_files",
    "web_search",
    "ask_a_person",
    "contact_records",
    "contact_function",
    "fill_form",
}


def _uow_factory() -> None:
    raise AssertionError("no unit of work is opened to build a stranger's tools")


def _kept_specs() -> list:
    specs = [spec for name in OUTSIDER_TOOLSETS for spec in STATIC_SPECS[name]]
    return [
        *specs,
        *contact_specs(uow_factory=_uow_factory),
        *form_specs(uow_factory=_uow_factory),
    ]


def test_the_kept_toolsets_are_exactly_these():
    """Widening this is a decision about strangers, not a side effect."""
    assert OUTSIDER_TOOLSETS == {
        AgentToolset.POD,
        AgentToolset.WEB_SEARCH,
        AgentToolset.MESSAGING,
    }


def test_exactly_the_decided_tools_are_allowed():
    """A tool added to a kept toolset is withheld until somebody allows it."""
    allowed = {spec.name for spec in _kept_specs() if spec.policy.outsider_allowed}
    assert allowed == ALLOWED


def test_web_fetch_and_the_writes_are_withheld():
    withheld = {spec.name for spec in _kept_specs() if not spec.policy.outsider_allowed}
    assert {"web_fetch", "pod_write_record", "pod_write_file", "pod_edit_file"} <= (
        withheld
    )


def _stranger_deps(**overrides) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        org_id=uuid4(),
        conversation_id=uuid4(),
        is_pod_default_agent=True,
        audience=Audience.contact(uuid4()),
        **overrides,
    )


def _strangers_conversation(deps: ToolContext, **metadata: object) -> Conversation:
    assert isinstance(deps.audience.contact_id, UUID)
    return Conversation(
        id=deps.conversation_id,
        user_id=deps.user_id,
        pod_id=deps.pod_id,
        contact_id=deps.audience.contact_id,
        metadata=metadata,
    )


@pytest.mark.anyio
async def test_an_assembled_strangers_run_is_offered_only_the_allow_list():
    """Through the real catalog and capabilities, as the harness builds a run."""
    deps = _stranger_deps()
    specs = await specs_for(
        agent=None,
        conversation=_strangers_conversation(deps),
        ctx=deps,
        surface=ToolSurface.IN_PROCESS,
        uow_factory=lambda: SimpleNamespace(),
    )
    capabilities = harness_capabilities(
        ctx=deps,
        specs=specs,
        runtime=ToolRuntime(specs),
        enable_prompt_caching=False,
    )

    offered: set[str] = set()

    def model_fn(messages, info: AgentInfo):
        offered.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("done")])

    model = FunctionModel(
        model_fn, profile=ModelProfile(tool_deferral_mode="with_tool_search")
    )
    await Agent(model, capabilities=capabilities).run("hi", deps=deps)

    assert offered, "the run was offered nothing at all"
    assert offered <= ALLOWED, sorted(offered - ALLOWED)
    assert "web_fetch" not in offered
    assert "ask_a_person" in offered


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("conversation_kind", "offered"),
    [("DM", True), ("EMAIL", True), ("CHANNEL", False)],
    ids=["private-chat", "email-thread", "group"],
)
async def test_a_contact_is_offered_their_own_rows_only_where_they_alone_read_it(
    conversation_kind, offered
):
    """In a group, everybody in it would read what the agent quoted back."""
    deps = _stranger_deps()

    deps.place_kind = conversation_kind
    specs = await specs_for(
        agent=None,
        conversation=_strangers_conversation(deps),
        ctx=deps,
        surface=ToolSurface.IN_PROCESS,
        uow_factory=lambda: SimpleNamespace(),
    )

    names = {spec.name for spec in specs}
    assert ("contact_records" in names) is offered
    assert ("contact_function" in names) is offered


@pytest.mark.anyio
async def test_the_runtime_refuses_an_off_list_tool_whatever_it_is_handed():
    """Whatever list a caller built, a stranger's run does not reach the shell."""
    runtime = ToolRuntime(workspace_cli.SPECS)

    refused = await runtime.call("exec_command", {"cmd": "ls"}, _stranger_deps())

    assert refused == {"success": False, "error": WITHHELD_MESSAGE}


@pytest.mark.anyio
async def test_no_workspace_is_opened_for_a_strangers_run():
    """The owner's sandbox: their files, env, a token minted for them."""

    class _Runtime:
        async def get_session(self, **_kwargs):
            raise AssertionError("the owner's sandbox was opened for a stranger")

        get_host_session = get_session

    with pytest.raises(OutsiderRunRefused):
        await get_workspace_session(
            _stranger_deps(), session_id=None, close_on_exit=True, runtime=_Runtime()
        )
    with pytest.raises(OutsiderRunRefused):
        _stranger_deps().file_manager  # noqa: B018 - the property is the test


def test_a_members_run_reaches_its_workspace_as_before():
    assert refuse_owner_workspace(SimpleNamespace(audience=Audience.member())) is None
    assert refuse_owner_workspace(SimpleNamespace()) is None
