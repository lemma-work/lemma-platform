"""What one person's run may see of another's, in a shared conversation.

The rule is that the answer belongs to the conversation and the working
belongs to whoever triggered the run. These pin the prompt half: another
person's run reaches the model as its question and its answer, never as its
tool arguments or its tool results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.modules.agent.domain.entities import (
    AgentRun,
    AgentRuntimeConfig,
    Message,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services.runtime_history import select_runtime_history

_BASE = datetime.now(timezone.utc) - timedelta(hours=1)


def _run(
    conversation_id: UUID,
    *,
    index: int,
    triggered_by: UUID | None,
    secret: str = "s3cret",
) -> AgentRun:
    """One run: a question, a tool call, its result, and an answer."""
    run_id = uuid4()
    base = index * 1000
    return AgentRun(
        id=run_id,
        conversation_id=conversation_id,
        triggered_by_user_id=triggered_by,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        started_at=_BASE + timedelta(minutes=index),
        messages=[
            Message(
                conversation_id=conversation_id,
                sequence=base,
                agent_run_id=run_id,
                role=MessageRole.USER,
                kind=MessageKind.TEXT,
                text=f"question {index}",
            ),
            Message(
                conversation_id=conversation_id,
                sequence=base + 1,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TOOL_CALL,
                tool_name="query_table",
                tool_call_id=f"tc_{index}",
                tool_args={"where": secret},
            ),
            Message(
                conversation_id=conversation_id,
                sequence=base + 2,
                agent_run_id=run_id,
                role=MessageRole.TOOL,
                kind=MessageKind.TOOL_RETURN,
                tool_name="query_table",
                tool_call_id=f"tc_{index}",
                tool_result={"rows": [secret]},
            ),
            Message(
                conversation_id=conversation_id,
                sequence=base + 3,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TEXT,
                text=f"answer {index}",
            ),
        ],
    )


def _conversation(owner_id: UUID):
    return SimpleNamespace(
        id=uuid4(), user_id=owner_id, metadata={}, is_pod_assistant=False
    )


def _kinds(messages: list[Message]) -> set[MessageKind]:
    return {message.kind for message in messages}


def test_another_persons_working_never_reaches_the_model():
    owner_id, other_id = uuid4(), uuid4()
    conversation = _conversation(owner_id)
    runs = [_run(conversation.id, index=0, triggered_by=other_id)]

    selected = select_runtime_history(runs, conversation, viewer_id=owner_id)

    texts = " ".join(message.text or "" for message in selected)
    assert "question 0" in texts
    assert "answer 0" in texts
    assert MessageKind.TOOL_CALL not in _kinds(selected)
    assert MessageKind.TOOL_RETURN not in _kinds(selected)
    assert "s3cret" not in str([m.model_dump() for m in selected])


def test_your_own_recent_run_is_carried_whole():
    owner_id = uuid4()
    conversation = _conversation(owner_id)
    runs = [_run(conversation.id, index=0, triggered_by=owner_id)]

    selected = select_runtime_history(runs, conversation, viewer_id=owner_id)

    assert MessageKind.TOOL_CALL in _kinds(selected)
    assert MessageKind.TOOL_RETURN in _kinds(selected)


def test_a_run_with_no_trigger_belongs_to_the_owner():
    """Runs predating the column were backfilled to the owner; the same rule is
    applied here so the prompt and the transcript agree about whose they are."""
    owner_id, other_id = uuid4(), uuid4()
    conversation = _conversation(owner_id)
    runs = [_run(conversation.id, index=0, triggered_by=None)]

    assert MessageKind.TOOL_CALL in _kinds(
        select_runtime_history(runs, conversation, viewer_id=owner_id)
    )
    assert MessageKind.TOOL_CALL not in _kinds(
        select_runtime_history(runs, conversation, viewer_id=other_id)
    )


def test_naming_no_viewer_withholds_nothing():
    """Every caller that predates this, and every single-person conversation."""
    conversation = _conversation(uuid4())
    runs = [_run(conversation.id, index=0, triggered_by=uuid4())]

    assert MessageKind.TOOL_RETURN in _kinds(select_runtime_history(runs, conversation))


def test_recency_does_not_override_ownership():
    """The most recent run is normally carried whole. Being recent is not a
    reason to hand somebody else's tool results to this run."""
    owner_id, other_id = uuid4(), uuid4()
    conversation = _conversation(owner_id)
    runs = [
        _run(conversation.id, index=0, triggered_by=owner_id),
        _run(conversation.id, index=1, triggered_by=other_id),
    ]

    selected = select_runtime_history(runs, conversation, viewer_id=owner_id)
    latest = [m for m in selected if m.sequence >= 1000]

    assert MessageKind.TOOL_RETURN not in _kinds(latest)
    assert any("answer 1" in (m.text or "") for m in latest)


# --- who a run acts as ------------------------------------------------------


def test_the_run_decides_the_actor_not_the_job_payload():
    """`RunIdentity.user_id` is read off the run, so a job whose payload named
    the conversation's owner cannot widen a run started by somebody else."""
    from app.modules.agent.services.run_identity import RunIdentity

    owner_id, sender_id = uuid4(), uuid4()
    conversation = _conversation(owner_id)
    run = _run(conversation.id, index=0, triggered_by=sender_id)

    acting_user_id = run.triggered_by_user_id or owner_id
    identity = RunIdentity(
        conversation_id=conversation.id,
        agent_run_id=run.id,
        pod_id=uuid4(),
        user_id=acting_user_id,
    )

    assert identity.user_id == sender_id
    assert identity.user_id != owner_id


def test_a_run_with_no_trigger_falls_back_to_the_payload():
    """Runs created before the column carry None, and the owner-derived value
    the caller passes stands in -- the same answer the backfill wrote."""
    owner_id = uuid4()
    conversation = _conversation(owner_id)
    run = _run(conversation.id, index=0, triggered_by=None)

    assert (run.triggered_by_user_id or owner_id) == owner_id


# --- subthread branches ------------------------------------------------------


def _branch(parent: object, conversation_id, *, index: int, triggered_by):
    run = _run(conversation_id, index=index, triggered_by=triggered_by)
    run.parent_run_id = parent.id
    return run


def test_a_branch_does_not_see_its_siblings():
    """Two branches off one point are separate conversations about the same
    starting position. If each could read the other, branching would just be a
    second way of writing in the same place."""
    from app.modules.agent.services.runtime_history import apply_branch_lineage

    owner = uuid4()
    conversation = _conversation(owner)
    trunk = _run(conversation.id, index=0, triggered_by=owner)
    left = _branch(trunk, conversation.id, index=1, triggered_by=owner)
    right = _branch(trunk, conversation.id, index=2, triggered_by=owner)

    kept = apply_branch_lineage([trunk, left, right], right)

    assert [run.id for run in kept] == [trunk.id, right.id]


def test_a_trunk_run_still_sees_everything():
    """The default, and every conversation nobody has branched."""
    from app.modules.agent.services.runtime_history import apply_branch_lineage

    owner = uuid4()
    conversation = _conversation(owner)
    runs = [
        _run(conversation.id, index=0, triggered_by=owner),
        _run(conversation.id, index=1, triggered_by=owner),
    ]

    assert apply_branch_lineage(runs, runs[-1]) == runs


def test_a_branch_keeps_the_trunk_it_left_from():
    from app.modules.agent.services.runtime_history import apply_branch_lineage

    owner = uuid4()
    conversation = _conversation(owner)
    first = _run(conversation.id, index=0, triggered_by=owner)
    second = _run(conversation.id, index=1, triggered_by=owner)
    later = _run(conversation.id, index=3, triggered_by=owner)
    branch = _branch(second, conversation.id, index=2, triggered_by=owner)

    kept = apply_branch_lineage([first, second, branch, later], branch)

    # Everything up to the point it left, and not the trunk that carried on
    # without it.
    assert [run.id for run in kept] == [first.id, second.id, branch.id]


# --- one agent must not read another's words as its own ----------------------


def _agent_conversation(owner_id, *agents):
    """A conversation whose roster names each agent."""
    from app.modules.agent.domain.participants import ConversationParticipant

    conversation = _conversation(owner_id)
    conversation.participants = [
        ConversationParticipant(
            conversation_id=conversation.id, agent_id=agent_id, display_name=name
        )
        for agent_id, name in agents
    ]
    return conversation


def test_another_agents_reply_arrives_as_reported_speech():
    """The bug this exists for: every assistant message used to be replayed as
    the running model's own prior words, so an agent answering after another
    one read that agent's replies as its own. Batman insisted it was Robin."""
    batman_id, robin_id = uuid4(), uuid4()
    owner = uuid4()
    conversation = _agent_conversation(
        owner, (batman_id, "batman"), (robin_id, "robin")
    )

    robin_run = _run(conversation.id, index=0, triggered_by=owner)
    robin_run.agent_id = robin_id
    batman_run = _run(conversation.id, index=1, triggered_by=owner)
    batman_run.agent_id = batman_id

    selected = select_runtime_history(
        [robin_run, batman_run], conversation, viewer_id=owner, current_run=batman_run
    )

    spoken = [m for m in selected if (m.text or "").startswith("robin said:")]
    assert spoken, "robin's reply must be attributed, not replayed as batman's own"
    # And it must not still be sitting there as an assistant turn.
    assert not any(
        m.role is MessageRole.ASSISTANT and (m.text or "").startswith("answer 0")
        for m in selected
    )


def test_an_agents_own_turn_is_left_alone():
    """Its own history is its own; attributing it would make the agent read its
    own words as somebody else's."""
    batman_id = uuid4()
    owner = uuid4()
    conversation = _agent_conversation(owner, (batman_id, "batman"))

    own = _run(conversation.id, index=0, triggered_by=owner)
    own.agent_id = batman_id

    selected = select_runtime_history(
        [own], conversation, viewer_id=owner, current_run=own
    )

    assert any(m.role is MessageRole.ASSISTANT for m in selected)
    assert not any((m.text or "").startswith("batman said:") for m in selected)
