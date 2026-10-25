"""A fact mentioned in passing: answered first, kept after.

Against the real database, datastore and authorization -- the parts that decide
whether a kept fact lands where the next conversation reads it back, and that a
unit test of the pass would have to stand in for.

The run is the deterministic e2e model answering in one step, so what this
proves is the shape around it: the turn ends on its answer with nothing before
it, the completed run leaves exactly one ``agent.memory`` item for the person
it worked for, and the pass that item runs writes the fact into that person's
memory with the agent's own tools.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import status
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.events.turn_payloads import MemoryPassWork
from app.modules.agent.infrastructure.harnesses.mock_model import build_mock_model
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.agent_memory_brief import AgentMemoryBriefBuilder
from app.modules.agent.services.memory_pass import MemoryPass, MemoryPassOutcome
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    append,
    finish,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    rows,
    scalar,
    turn_pod,
    turn_registry,
)
from app.modules.test_support.e2e.scripted_model import (
    script_model_error,
    script_text,
    script_tool_call,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

ASKED = (
    "I run a 12-person design studio. Give me a short checklist (max 6 items) "
    "for onboarding a new client project."
)
ANSWER = "1. Kickoff call\n2. Shared brief\n3. Access to tools"
#: Everything but the pass: the turn runs, its own follow-ups run, and the
#: memory item -- AUTOMATION, like the title -- stays queued to be looked at.
WITHOUT_THE_PASS = [WorkClass.INTERACTIVE, WorkClass.EVENTS, WorkClass.DELIVERY]
PATHS = agent_memory_paths_for_name(DEFAULT_POD_AGENT_NAME)
STUDIO_NOTE = f"{PATHS.personal_agent_folder}/studio.md"


def _uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def _scripted(turns: list[dict]) -> MemoryPass:
    """A pass whose model answers with ``turns``, one per request."""
    model = build_mock_model(SimpleNamespace(metadata={"mock_llm_script": turns}))
    return MemoryPass(_uow_factory(), model_for=lambda _resolved: model)


async def _ask_the_pod_assistant(pod: TurnPod) -> tuple[str, str]:
    """A plain question to the pod's own assistant, answered in one step."""
    response = await pod.client.post(
        f"/pods/{pod.pod_id}/conversations",
        json={
            "title": "Onboarding",
            "metadata": {"mock_llm_script": [script_text(ANSWER)]},
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    conversation_id = response.json()["id"]
    run_id = (await pod.append(conversation_id, ASKED))["agent_run_id"]
    return conversation_id, run_id


async def _memory_items(run_id: str) -> list[tuple]:
    return await rows(
        "SELECT payload, class, concurrency_key FROM work_items WHERE dedup_key = :key",
        key=f"memory-pass:{run_id}",
    )


async def _read(pod: TurnPod, path: str) -> str | None:
    response = await pod.client.get(
        f"/pods/{pod.pod_id}/datastore/files/download", params={"path": path}
    )
    if response.status_code == status.HTTP_404_NOT_FOUND:
        return None
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.content.decode("utf-8")


@pytest.mark.asyncio
async def test_a_fact_said_in_passing_is_answered_first_and_kept_after(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod = await turn_pod(authenticated_client, fixed_test_org["id"])
    conversation_id, run_id = await _ask_the_pod_assistant(pod)

    await drain(turn_registry(), classes=WITHOUT_THE_PASS)

    # Nothing before the answer: no memory list, read or write in the turn.
    said = [m for m in await pod.messages(conversation_id) if m["role"] != "user"]
    assert [(m["kind"], m["text"]) for m in said] == [("TEXT", ANSWER)]
    # The completed run owes one pass, for the person it answered.
    [(payload, work_class, concurrency_key)] = await _memory_items(run_id)
    assert payload == {
        "conversation_id": conversation_id,
        "run_id": run_id,
        "user_id": fixed_test_user["id"],
    }
    assert work_class == "automation"
    assert concurrency_key == f"memory:{pod.pod_id}"

    outcome = await _scripted(
        [
            script_tool_call(
                "pod_write_file",
                {
                    "path": STUDIO_NOTE,
                    "content": "# Studio\n- Runs a 12-person design studio.\n",
                },
                tool_call_id="keep-note",
            ),
            script_tool_call(
                "pod_write_file",
                {
                    "path": PATHS.personal_index,
                    "content": f"- studio: `{STUDIO_NOTE}` — runs a design studio\n",
                },
                tool_call_id="keep-pointer",
            ),
            script_text("Kept that they run a 12-person design studio."),
        ]
    ).keep(
        conversation_id=UUID(conversation_id),
        run_id=UUID(run_id),
        user_id=UUID(fixed_test_user["id"]),
    )

    assert outcome is MemoryPassOutcome.KEPT
    assert "12-person design studio" in (await _read(pod, STUDIO_NOTE) or "")
    # Where the next conversation with this assistant reads it back.
    async with _uow_factory()() as uow:
        assistant = await AgentRepository(uow).get(UUID(pod.pod_id))
    assert assistant is not None
    section = await AgentMemoryBriefBuilder(_uow_factory()).build(
        agent=assistant,
        pod_id=UUID(pod.pod_id),
        user_id=UUID(fixed_test_user["id"]),
    )
    assert STUDIO_NOTE in section


@pytest.mark.asyncio
async def test_a_turn_with_nothing_worth_keeping_leaves_memory_untouched(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The whole path, the pass included, on the deployment's own handler: the
    e2e model answers in plain text, which is a pass that keeps nothing."""
    pod = await turn_pod(authenticated_client, fixed_test_org["id"])
    _conversation_id, run_id = await _ask_the_pod_assistant(pod)

    await drain(turn_registry())

    # It ran, as its own spend rather than the run's, and nothing is left over.
    assert await scalar(
        "SELECT count(*) FROM usage_records "
        "WHERE source_type = 'agent_memory' AND source_id = :run_id",
        run_id=run_id,
    )
    assert await _memory_items(run_id) == []
    assert await _read(pod, PATHS.personal_index) is None
    assert await _read(pod, STUDIO_NOTE) is None


@pytest.mark.asyncio
async def test_an_agent_without_memory_owes_no_pass(
    authenticated_client, fixed_test_org
):
    pod = await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])
    conversation_id = await pod.conversation([script_text(ANSWER)])
    run_id = (await pod.append(conversation_id, ASKED))["agent_run_id"]

    await drain(turn_registry(), classes=WITHOUT_THE_PASS)

    assert await pod.run_status(run_id) == "COMPLETED"
    assert await _memory_items(run_id) == []


def _keeping(sent: list[str]) -> MemoryPass:
    """A pass whose model records what it was sent, keeps a note, and stops."""

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(parts=[TextPart("Kept.")])
        sent.extend(
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "pod_write_file",
                    {"path": PATHS.personal_index, "content": "- kept from the turn\n"},
                )
            ]
        )

    model = FunctionModel(respond)
    return MemoryPass(_uow_factory(), model_for=lambda _resolved: model)


async def _the_pass_for(run_id: UUID | str) -> MemoryPassWork:
    [(payload, _cls, _key)] = await _memory_items(str(run_id))
    return MemoryPassWork.model_validate(payload)


@pytest.mark.asyncio
async def test_a_retried_turn_is_kept_from_what_the_failed_run_was_asked(scenario):
    """The retry holds none of the person's words: they stay on the run that
    failed, which owed no pass. Without them the retry's pass had nothing."""
    seeded = await seeded_conversation(scenario, toolsets=["MEMORY", "POD"])
    failed = await running_run(seeded)
    await append(seeded, failed, MessageDraft.of_text(ASKED, role=MessageRole.USER))
    await finish(failed, status=AgentRunStatus.FAILED)
    retried = await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/retry"
    )
    assert retried.status_code == status.HTTP_200_OK, retried.text
    await drain(turn_registry(), classes=WITHOUT_THE_PASS)
    owed = await _the_pass_for(retried.json()["agent_run_id"])

    sent: list[str] = []
    outcome = await _keeping(sent).keep(
        conversation_id=owed.conversation_id, run_id=owed.run_id, user_id=owed.user_id
    )

    assert outcome is MemoryPassOutcome.KEPT
    assert "12-person design studio" in "\n".join(sent)


@pytest.mark.asyncio
async def test_a_configured_pass_model_runs_the_pass_without_the_runs_settings(
    scenario,
):
    """`AGENT_MEMORY_PASS_MODEL` moves the pass off the run's own model. The
    run's generation settings are for the run's model, so the configured one
    is sent none; the run's model is not asked at all."""
    seeded = await seeded_conversation(scenario, toolsets=["MEMORY", "POD"])
    run = await running_run(seeded)
    await append(seeded, run, MessageDraft.of_text(ASKED, role=MessageRole.USER))
    await finish(run, status=AgentRunStatus.COMPLETED)

    settings_sent: list[object] = []

    def configured(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings_sent.append(info.model_settings)
        return ModelResponse(parts=[TextPart("nothing")])

    def run_model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise AssertionError("the run's own model was asked")

    async def choose(fallback, _organization_id, _user_id):
        assert fallback is run_side
        return FunctionModel(configured)

    run_side = FunctionModel(run_model)
    outcome = await MemoryPass(
        _uow_factory(), model_for=lambda _resolved: run_side, pass_model=choose
    ).keep(conversation_id=seeded.conversation_id, run_id=run, user_id=seeded.user_id)

    assert outcome is MemoryPassOutcome.NOTHING
    assert settings_sent == [None]


@pytest.mark.asyncio
async def test_an_answer_to_the_agents_question_is_kept_by_the_run_it_resumed(
    scenario,
):
    """The answer is stored beside the question on the run that paused, whose
    pass ran before anyone answered; the run it resumes holds neither."""
    seeded = await seeded_conversation(scenario, toolsets=["MEMORY", "POD"])
    paused = await running_run(seeded)
    await append(
        seeded,
        paused,
        MessageDraft.of_text("Plan our weekly team sync.", role=MessageRole.USER),
    )
    question = await pausing_call(
        seeded,
        paused,
        tool_name="ask_user",
        tool_args={
            "questions": [
                {
                    "question": "Which city is the team in?",
                    "header": "City",
                    "options": [{"label": "Lisbon"}, {"label": "Pune"}],
                }
            ]
        },
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)
    answered = await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"
        f"/approvals/{question}/decision",
        json={"decision": "APPROVE_ONCE", "response": {"answers": {"City": "Lisbon"}}},
    )
    assert answered.status_code == status.HTTP_200_OK, answered.text
    await drain(turn_registry(), classes=WITHOUT_THE_PASS)
    resumed = (await runs(seeded.conversation_id))[-1]
    owed = await _the_pass_for(resumed.id)

    sent: list[str] = []
    outcome = await _keeping(sent).keep(
        conversation_id=owed.conversation_id, run_id=owed.run_id, user_id=owed.user_id
    )

    assert outcome is MemoryPassOutcome.KEPT
    assert "Which city is the team in?" in sent[0] and "Lisbon" in sent[0]


@pytest.mark.asyncio
async def test_a_pass_whose_model_fails_changes_neither_the_answer_nor_memory(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The provider failing on the pass is the work runtime's to retry; the
    person already has their answer, and memory is as it was."""
    pod = await turn_pod(authenticated_client, fixed_test_org["id"])
    conversation_id, run_id = await _ask_the_pod_assistant(pod)
    await drain(turn_registry(), classes=WITHOUT_THE_PASS)

    failing = _scripted(
        [script_model_error("model_http", message="overloaded", status_code=503)]
    )
    with pytest.raises(ModelHTTPError):
        await failing.keep(
            conversation_id=UUID(conversation_id),
            run_id=UUID(run_id),
            user_id=UUID(fixed_test_user["id"]),
        )

    assert await pod.run_status(run_id) == "COMPLETED"
    answers = [
        m["text"]
        for m in await pod.messages(conversation_id)
        if m["role"] == "assistant" and m["kind"] == "TEXT"
    ]
    assert answers == [ANSWER]
    assert await _read(pod, PATHS.personal_index) is None
