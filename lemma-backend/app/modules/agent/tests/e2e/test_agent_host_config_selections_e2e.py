"""A coding agent's own options, chosen per pod, teammate and conversation.

A harness publishes options per model: Claude Code's ``max`` effort exists for
one model and not another, so the host lists it under that model's
``lemma_options``. These drive the real pieces end to end, against a real
pairing, a real publish over the link and the real database:

- a save is checked against the options of the model the runtime will use;
- a turn layers pod default, teammate and conversation, narrowest per option,
  and dispatch keeps only what the harness offers for the run's model now;
- a publish rewrites the profiles of the harness it reported, and no others;
- a host's ``config_update`` reaches the conversation as one flat notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from app.modules.agent.application.agents.manage_runtime_profiles import (
    RuntimeProfileCreation,
)
import pytest
from sqlalchemy import select, update
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.domain.host import (
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunSpec,
    AgentHostRunState,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    ConversationStatus,
    ConversationType,
)
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.repository import (
    AgentHostRepository,
)
from app.modules.agent.agent_host.harness.harness import (
    AgentHostRunConfig,
    RemoteHarness,
)
from app.modules.agent.agent_host.harness.run_config import (
    agent_host_run_config,
    offered_selections,
)
from app.modules.agent.agent_host.harness.run_window import (
    DispatchedRun,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.repositories.agent_repository import (
    AgentRepository,
)
from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.services.runtime_availability import turn_runtime
from app.modules.agent.services.runtime_profile_service import (
    runtime_profile_service_for,
)
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
    publish_harnesses,
    stale_after,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = pytest.mark.e2e


def _effort(*values: str) -> dict[str, object]:
    return {
        "id": "effort",
        "name": "Effort",
        "category": "thought_level",
        "options": [{"name": value.title(), "value": value} for value in values],
    }


_MODE = {
    "id": "mode",
    "name": "Mode",
    "category": "mode",
    "options": [
        {"name": "Default", "value": "default"},
        {"name": "Plan", "value": "plan"},
    ],
}


def _harness_options(*, opus_efforts: tuple[str, ...] | None) -> list[dict]:
    """The machine's default model tops out at ``high``; ``opus``, when it is
    offered, has its own list."""
    models: list[dict[str, object]] = [{"name": "Sonnet", "value": "sonnet"}]
    if opus_efforts is not None:
        models.append(
            {
                "name": "Opus",
                "value": "opus",
                "lemma_options": [_effort(*opus_efforts), _MODE],
            }
        )
    return [
        {"id": "model", "name": "Model", "category": "model", "options": models},
        _effort("low", "high"),
        _MODE,
    ]


def _harness_snapshot(revision: str, config_options: list[dict]) -> dict:
    return {
        "harness_key": "claude-code",
        "display_name": "Claude Code",
        "adapter_version": "1.0.0",
        "health": "READY",
        "capabilities": {"load_session": True},
        "config_revision": revision,
        "config_options": config_options,
        "stale_after": stale_after(),
    }


async def _machine_with_profile(
    db_session,
    scenario: E2EScenario,
    *,
    display_name: str,
    default_model_name: str | None,
    config_selections: dict[str, object] | None = None,
) -> tuple[dict, AgentRuntimeProfile]:
    """A paired, online machine and a profile built the way Settings builds one."""
    machine = await paired_machine(
        scenario,
        display_name=display_name,
        harness_key="claude-code",
        config_options=_harness_options(opus_efforts=("low", "high", "max")),
    )
    # Admission wants a fresh heartbeat, which the link's `control` frame
    # stamps and this test does not send.
    await db_session.execute(
        update(AgentHostModel)
        .where(AgentHostModel.id == machine["host_id"])
        .values(status="ONLINE", last_seen_at=datetime.now(timezone.utc))
    )
    await db_session.flush()
    uow = SqlAlchemyUnitOfWork(db_session)
    profile = await RuntimeProfileCreation(
        AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
        AgentHostRepository(uow),
    ).create_agent_host_profile(
        harness_id=machine["harness_id"],
        organization_id=UUID(scenario.org_id),
        user_id=UUID(scenario.owner_user["id"]),
        scope=RuntimeProfileScope.PERSONAL,
        name=f"{display_name} {uuid7().hex[:6]}",
        default_model_name=default_model_name,
        config_selections=config_selections,
    )
    await db_session.commit()
    return machine, profile


@pytest.mark.asyncio
async def test_a_saved_selection_is_checked_against_the_model_it_will_run_on(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="SelectionSave")
    _machine, profile = await _machine_with_profile(
        db_session, scenario, display_name="save e2e", default_model_name="sonnet"
    )
    client = scenario.owner_client
    agents = f"/pods/{scenario.pod_id}/agents"

    # `max` exists for opus only; the profile's own model is sonnet.
    on_default_model = await client.post(
        agents,
        json={
            "name": f"refused-{uuid7().hex[:8]}",
            "instruction": "Help.",
            "agent_runtime": {
                "profile_id": profile.id,
                "config_selections": {"effort": "max"},
            },
        },
    )
    assert on_default_model.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid value" in on_default_model.json()["message"]

    name = f"opus-{uuid7().hex[:8]}"
    on_opus = await client.post(
        agents,
        json={
            "name": name,
            "instruction": "Help.",
            "agent_runtime": {
                "profile_id": profile.id,
                "model_name": "opus",
                "config_selections": {"effort": "max"},
            },
        },
    )
    assert on_opus.status_code == status.HTTP_201_CREATED, on_opus.text

    unknown = await client.patch(
        f"{agents}/{name}",
        json={
            "agent_runtime": {
                "profile_id": profile.id,
                "model_name": "opus",
                "config_selections": {"temperature": "hot"},
            }
        },
    )
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unknown Agent Host configuration" in unknown.json()["message"]

    conversation = await client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={
            "title": "selections",
            "agent_runtime": {
                "profile_id": profile.id,
                "config_selections": {"effort": "max"},
            },
        },
    )
    assert conversation.status_code == status.HTTP_400_BAD_REQUEST

    pod = f"/pods/{scenario.pod_id}"
    refused_default = await client.put(
        pod,
        json={
            "config": {
                "default_runtime": {
                    "profile_id": profile.id,
                    "config_selections": {"effort": "max"},
                }
            }
        },
    )
    assert refused_default.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid value" in refused_default.json()["message"]

    saved_default = await client.put(
        pod,
        json={
            "config": {
                "default_runtime": {
                    "profile_id": profile.id,
                    "config_selections": {"mode": "plan"},
                }
            }
        },
    )
    assert saved_default.status_code == status.HTTP_200_OK, saved_default.text
    assert saved_default.json()["config"]["default_runtime"] == {
        "profile_id": profile.id,
        "config_selections": {"mode": "plan"},
    }


async def _admit(
    db_session,
    *,
    machine: dict,
    run_config: AgentHostRunConfig,
    conversation_id: UUID,
    revision: str,
) -> dict:
    """Admit one run with this config, and return the START_RUN it queued."""
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    command = await AgentHostDispatchRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).enqueue_run(
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        runtime_profile_id=run_config.runtime_profile_id,
        run_spec=AgentHostRunSpec(
            agent_run_id=run.id,
            conversation_id=conversation_id,
            harness_id=machine["harness_id"],
            profile_revision=revision,
            model_name=run_config.model_name,
            config_selections=run_config.config_selections,
            system_prompt="Selections test.",
            prompt=[{"type": "text", "text": "Hello."}],
            run_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
        ),
        encrypted_mcp_payload={"encrypted": True},
    )
    assert command.kind == AgentHostCommandKind.START_RUN.value
    payload = dict(command.payload)
    # One active run per conversation; the next admission is a later turn.
    run.status = "COMPLETED"
    await db_session.commit()
    return payload


async def _dispatchable(
    db_session, *, machine: dict, run_config: AgentHostRunConfig
) -> AgentHostRunConfig:
    """What dispatch keeps of ``run_config``, against the harness as stored now."""
    db_session.expire_all()
    harness = await AgentHostRepository(SqlAlchemyUnitOfWork(db_session)).get_harness(
        harness_id=machine["harness_id"]
    )
    assert harness is not None
    return offered_selections(
        run_config,
        config_options=list(harness.config_options or []),
        agent_run_id=uuid7(),
    )


@pytest.mark.asyncio
async def test_a_turn_layers_its_selections_and_dispatch_keeps_what_is_offered(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="SelectionTurn")
    machine, profile = await _machine_with_profile(
        db_session,
        scenario,
        display_name="turn e2e",
        default_model_name="sonnet",
        config_selections={"effort": "low"},
    )
    client = scenario.owner_client
    pod_default = await client.put(
        f"/pods/{scenario.pod_id}",
        json={
            "config": {
                "default_runtime": {
                    "profile_id": profile.id,
                    "config_selections": {"mode": "plan"},
                }
            }
        },
    )
    assert pod_default.status_code == status.HTTP_200_OK, pod_default.text
    agent_name = f"layered-{uuid7().hex[:8]}"
    teammate = await client.post(
        f"/pods/{scenario.pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Help.",
            "agent_runtime": {
                "profile_id": profile.id,
                "model_name": "opus",
                "config_selections": {"effort": "high", "mode": "default"},
            },
        },
    )
    assert teammate.status_code == status.HTTP_201_CREATED, teammate.text
    created = await client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={
            "title": "layered",
            "agent_name": agent_name,
            "agent_runtime": {
                "profile_id": profile.id,
                "model_name": "opus",
                "config_selections": {"effort": "max"},
            },
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    conversation_id = UUID(created.json()["id"])

    uow = SqlAlchemyUnitOfWork(db_session)
    conversation = await Conversations(uow).get_conversation(conversation_id)
    agent = await AgentRepository(uow).get_by_pod_and_name(
        pod_id=UUID(scenario.pod_id), name=agent_name
    )
    assert conversation is not None and agent is not None
    runtime = await turn_runtime(uow, conversation, agent)

    # Narrowest wins per option: the conversation's effort over the teammate's,
    # the teammate's mode over the pod's.
    assert runtime.config_selections == {"effort": "max", "mode": "default"}

    resolved = await runtime_profile_service_for(uow).resolve(
        runtime=runtime,
        organization_id=UUID(scenario.org_id),
        user_id=UUID(scenario.owner_user["id"]),
    )
    run_config = agent_host_run_config(
        HarnessOptions(
            model_name=resolved.model_name_for_harness,
            runtime_profile=resolved.runtime_snapshot(),
        )
    )
    assert run_config.model_name == "opus"
    # Over the profile's own `effort: low`.
    assert run_config.config_selections == {"effort": "max", "mode": "default"}

    payload = await _admit(
        db_session,
        machine=machine,
        run_config=await _dispatchable(
            db_session, machine=machine, run_config=run_config
        ),
        conversation_id=conversation_id,
        revision="rev-1",
    )
    assert payload["model_name"] == "opus"
    assert payload["config_selections"] == {"effort": "max", "mode": "default"}

    # The machine stops offering `max` for opus. The next run is not refused:
    # the selection is dropped, and the host uses its own setting.
    republished = await publish_harnesses(
        scenario.async_client,
        machine,
        [_harness_snapshot("rev-2", _harness_options(opus_efforts=("low", "high")))],
    )
    assert republished["type"] == "harnesses_ok", republished
    payload = await _admit(
        db_session,
        machine=machine,
        run_config=await _dispatchable(
            db_session, machine=machine, run_config=run_config
        ),
        conversation_id=conversation_id,
        revision="rev-2",
    )
    assert payload["config_selections"] == {"mode": "default"}


@pytest.mark.asyncio
async def test_a_publish_refreshes_the_profiles_of_the_harness_it_reported_only(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="SelectionPublish")
    machine, refreshed = await _machine_with_profile(
        db_session,
        scenario,
        display_name="republishing machine",
        default_model_name="opus",
        config_selections={"effort": "max", "mode": "plan"},
    )
    _other, untouched = await _machine_with_profile(
        db_session,
        scenario,
        display_name="quiet machine",
        default_model_name="opus",
        config_selections={"effort": "max"},
    )

    # Opus is gone, and a model arrived.
    options = _harness_options(opus_efforts=None)
    options[0]["options"].append({"name": "Haiku", "value": "haiku"})
    published = await publish_harnesses(
        scenario.async_client, machine, [_harness_snapshot("rev-2", options)]
    )
    assert published["type"] == "harnesses_ok", published

    db_session.expire_all()
    rows = {
        str(row.id): row
        for row in (
            await db_session.execute(
                select(AgentRuntimeProfileModel).where(
                    AgentRuntimeProfileModel.id.in_(
                        [UUID(refreshed.id), UUID(untouched.id)]
                    )
                )
            )
        ).scalars()
    }
    after = rows[refreshed.id]
    assert [entry["name"] for entry in after.model_catalog] == ["sonnet", "haiku"]
    # Only what the machine owns moves. The pin and the selections are the
    # person's: a turn on the withdrawn pin is refused with that reason, and
    # dispatch drops `max` from a run whose model does not offer it.
    assert after.default_model_name == "opus"
    assert after.config["config_selections"] == {"effort": "max", "mode": "plan"}
    assert after.config["harness_snapshot_revision"] == "rev-2"

    other = rows[untouched.id]
    assert [entry["name"] for entry in other.model_catalog] == ["sonnet", "opus"]
    assert other.default_model_name == "opus"
    assert other.config["config_selections"] == {"effort": "max"}


@pytest.mark.asyncio
async def test_a_host_notice_reaches_the_conversation_as_a_flat_status(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="SelectionNotice")
    machine = await paired_machine(scenario, display_name="notice e2e")
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        state=AgentHostRunState.RUNNING,
        title="notice",
    )
    await db_session.commit()

    def at(sequence: int, kind: AgentHostEventType, payload: dict) -> AgentHostEvent:
        return AgentHostEvent(
            run_id=run_id, lease_epoch=1, sequence=sequence, type=kind, payload=payload
        )

    await AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session)).append_events(
        host_id=machine["host_id"],
        batch=AgentHostEventBatch(
            events=[
                at(
                    1,
                    AgentHostEventType.CONFIG_UPDATE,
                    {
                        "status": "selection_unavailable",
                        "option": "effort",
                        "requested_value": "max",
                        "detail": "The profile asks for effort = max, but no.",
                    },
                ),
                at(2, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"}),
            ]
        ),
    )
    pod_id = UUID(scenario.pod_id)
    conversation = Conversation(
        id=conversation_id,
        pod_id=pod_id,
        user_id=uuid7(),
        agent_id=uuid7(),
        title="notice",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
    )
    events = [
        event
        async for event in RemoteHarness(
            lambda: SqlAlchemyUnitOfWork(db_session)
        )._consume(
            agent_run_id=run_id,
            agent=Agent(
                id=uuid7(),
                pod_id=pod_id,
                user_id=uuid7(),
                name="helper",
                instruction="Help.",
            ),
            ctx=ToolContext(
                user_id=uuid7(), pod_id=pod_id, conversation_id=conversation_id
            ),
            conversation=conversation,
            options=HarnessOptions(model_name="opus"),
            run_config=AgentHostRunConfig(
                harness_id=machine["harness_id"],
                runtime_profile_id=uuid7(),
                config_selections={},
                wait_timeout_seconds=300,
                model_name=None,
            ),
            dispatch=DispatchedRun(
                harness_key="claude-code",
                event_timeout_seconds=30.0,
                credential_bounded=False,
            ),
        )
    ]

    notices = [
        event.data
        for event in events
        if event.type is AgentEventType.STATUS
        and event.data.get("status") == "config_update"
    ]
    assert len(notices) == 1
    assert notices[0]["notice"] == "selection_unavailable"
    assert notices[0]["detail"] == "The profile asks for effort = max, but no."
    assert notices[0]["option"] == "effort"
    assert events[-1].type is AgentEventType.COMPLETED
