"""Whether an Agent Host run may look at a PDF page, decided end to end.

Desktop's common configuration has no ``VISION_MODEL``: the model *is* the
harness -- Claude Code or Codex on the user's own machine -- and those read
images natively, so there is nothing to delegate to and nothing to configure.
Everything therefore rests on the MCP bridge deriving ``DIRECT`` from the run's
runtime. Get it wrong and ``pod_view_document_pages`` answers "no vision model
is configured" for a host that can see perfectly well.

The existing unit coverage mocks ``AgentRuntimeProfileService.resolve`` and
asserts the bridge uses its return value. That is worth having, and it cannot
fail for the reason this actually breaks: a mock always resolves. What decides
it in production is whether resolution *succeeds* against a real harness
profile -- one whose stored runtime is ``{"profile_id": ...}`` with no model
name, whose ``default_model_name`` may be unset, and whose catalog was frozen
before the host's ACP probe reported what it could do.

So nothing here is doubled below the bridge: a real paired host, a real
published harness, a real profile built from it, a real run, and the shipped
``_load_agent_context``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import update

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.infrastructure.agent_host_repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostModel
from app.modules.agent.services.conversation_mcp_service import ConversationMCPService
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)
from app.modules.agent.services.workspace_location import (
    resolve_pod_cwd,
    resolve_workspace_location,
)
from app.modules.agent.tests.e2e.agent_host_helpers import (
    paired_machine,
    stale_after,
)

pytestmark = pytest.mark.e2e


# The `model` category a real `claude-code` harness publishes. Copied from the
# live desktop install's `agent_host_harnesses` row, because the *shape* is the
# thing under test: it is what gives the profile a populated catalog while
# leaving `default_model_name` unset, which is precisely the state that reported
# every Agent Host run as unable to see.
_CLAUDE_CODE_MODEL_OPTION = {
    "id": "model",
    "name": "Model",
    "category": "model",
    "description": "Model for the session",
    "metadata": {"type": "select"},
    "current_value": "default",
    "options": [
        {"name": "Default (recommended)", "value": "default"},
        {"name": "Opus (1M context)", "value": "opus[1m]"},
    ],
}


async def _profile_for_a_host_that(db_session, scenario, *, reports_images: bool):
    """A harness profile built the way Models settings builds one."""
    machine = await paired_machine(
        scenario,
        display_name="vision e2e",
        harness_key="claude-code",
        capabilities={"load_session": True, "images": reports_images},
        config_options=[_CLAUDE_CODE_MODEL_OPTION],
    )
    # A paired host is only "accepting new runs" while its heartbeat is fresh,
    # and the heartbeat rides on the 25s long poll -- which a test cannot sit
    # through. Stamping it is the same thing that poll does, without the wait.
    await db_session.execute(
        update(AgentHostModel)
        .where(AgentHostModel.id == machine["host_id"])
        .values(status="ONLINE", last_seen_at=datetime.now(timezone.utc))
    )
    await db_session.flush()

    uow = SqlAlchemyUnitOfWork(db_session)
    service = AgentRuntimeProfileService(
        AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
        AgentHostRepository(uow),
    )
    profile = await service.create_agent_host_profile(
        harness_id=machine["harness_id"],
        organization_id=UUID(scenario.org_id),
        user_id=UUID(scenario.owner_user["id"]),
        scope=RuntimeProfileScope.PERSONAL,
        name="Claude Code",
    )
    await db_session.commit()
    return machine, profile


async def _vision_mode_for(db_session, scenario, profile):
    """Run the shipped bridge over a run pinned to this profile."""
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "vision"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = created.json()["id"]

    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        # Exactly what a dispatched run stores: a profile id and nothing else.
        # No model name, which is what makes the resolution load-bearing.
        agent_runtime={"profile_id": str(profile.id)},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.commit()

    _agent, _conversation, ctx = await ConversationMCPService()._load_agent_context(
        conversation_id=run.conversation_id, agent_run_id=run.id
    )
    return ctx.vision_mode, run


@pytest.mark.asyncio
async def test_a_host_that_reports_images_may_read_a_page_without_a_vision_model(
    db_session, scenario
):
    """The configuration desktop actually ships: a seeing harness, no VISION_MODEL."""
    await scenario.create_org_with_pod(name_prefix="Vision")
    _machine, profile = await _profile_for_a_host_that(
        db_session, scenario, reports_images=True
    )

    mode, _run = await _vision_mode_for(db_session, scenario, profile)

    assert mode is AgentVisionMode.DIRECT


@pytest.mark.asyncio
async def test_a_stale_catalog_does_not_outvote_a_host_that_learned_to_see(
    db_session, scenario
):
    """The ordering that actually happens, and the reason for the live lookup.

    A host registers before its ACP probe lands, so the profile's catalog is
    very often frozen with ``images: false``. The probe updates the *harness*
    moments later, but the copy taken into the catalog is only rebuilt when
    somebody edits the profile. Between those two points a Claude Code host
    reads images natively and is described as text-only -- and with no
    ``VISION_MODEL`` to fall back to, page viewing refuses outright.
    """
    await scenario.create_org_with_pod(name_prefix="Stale")
    machine, profile = await _profile_for_a_host_that(
        db_session, scenario, reports_images=False
    )

    # The catalog is genuinely stale: built from a harness that said no.
    assert all(
        "VISION" not in [str(c) for c in entry.capabilities]
        for entry in profile.model_catalog
    ), "fixture is wrong: the catalog should have been frozen without VISION"

    # Now the probe lands, exactly as the host reports it over ACP.
    republished = await scenario.async_client.put(
        "/agent-host/harnesses",
        json={
            "harnesses": [
                {
                    "harness_key": "claude-code",
                    "display_name": "Claude Code",
                    "adapter_version": "1.0.0",
                    "health": "READY",
                    "capabilities": {"load_session": True, "images": True},
                    "config_revision": "rev-2",
                    "config_options": [_CLAUDE_CODE_MODEL_OPTION],
                    "stale_after": stale_after(),
                }
            ]
        },
        headers={"Authorization": f"Bearer {machine['host_secret']}"},
    )
    assert republished.status_code == 200, republished.text

    mode, _run = await _vision_mode_for(db_session, scenario, profile)

    assert mode is AgentVisionMode.DIRECT


@pytest.mark.asyncio
async def test_the_tool_list_a_remote_harness_receives_actually_contains_view_image(
    db_session, scenario
):
    """The tool the prompts tell every agent to use, over the path it arrives on.

    `view_image` was appended to the toolset by the *runner*, and a remote
    harness never goes through the runner for tools -- it reaches them through
    the MCP server, which re-assembles the list from scratch. So no Agent Host
    run could call `view_image`, whatever its vision mode, while the run spec
    still advertised the tool because that list is the runner's copy.

    Meanwhile `prompts/web_search.md` and `web_fetch`'s own result message tell
    the model to view screenshots with it, unconditionally.

    This asks the shipped bridge for the list a real harness is handed.
    """
    await scenario.create_org_with_pod(name_prefix="ViewImage")
    _machine, profile = await _profile_for_a_host_that(
        db_session, scenario, reports_images=True
    )
    mode, run = await _vision_mode_for(db_session, scenario, profile)
    assert mode is AgentVisionMode.DIRECT

    tools = await ConversationMCPService().list_tools(
        conversation_id=run.conversation_id, agent_run_id=run.id
    )
    names = {tool.name for tool in tools}

    assert any("view_image" in name for name in names), sorted(names)


@pytest.mark.asyncio
async def test_the_bridge_hands_tools_the_directory_the_prompt_names(
    db_session, scenario
):
    """The agent's prompt and the agent's tools disagreed about where it is.

    `resolve_workspace_location` puts a conversation at
    `/workspace/c/<date>/<slug>`, stamps it into the conversation's metadata,
    and the prompt quotes it. The tools go wherever `ctx.get_workspace_cwd()`
    says -- and this bridge never set `workspace_cwd`, so it fell back to
    `/workspace/conversations/<uuid>`. For the in-process harness the prompt was
    true; for every remote harness it named a directory the tools never entered,
    which is why `pwd` disagreed with the Working Directory section.

    `pod_cwd` had the same hole, scattering pod writes under
    `/me/conversations/<uuid>` instead of the `/me/c/<date>/<slug>` the resolver
    mirrors the workspace to.
    """
    await scenario.create_org_with_pod(name_prefix="Cwd")
    _machine, profile = await _profile_for_a_host_that(
        db_session, scenario, reports_images=True
    )
    _mode, run = await _vision_mode_for(db_session, scenario, profile)

    _agent, conversation, ctx = await ConversationMCPService()._load_agent_context(
        conversation_id=run.conversation_id, agent_run_id=run.id
    )

    expected = resolve_workspace_location(conversation)
    assert ctx.get_workspace_cwd() == expected.cwd
    assert ctx.get_pod_cwd() == resolve_pod_cwd(conversation)
    # Named explicitly: this is the shape the tools used to get.
    assert not ctx.get_workspace_cwd().startswith("/workspace/conversations/")
