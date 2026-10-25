"""The brief's askable-pods section, per conversation.

It lists only the pods, and the ways, ``ask_teammate`` would allow from this
conversation: a task nobody started is offered only connected pods, a channel
none at all -- and a channel does not even read the list.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationOrigin, ConversationType
from app.modules.agent.infrastructure.pod_ask_queries import Teammate
from app.modules.agent.services.context_brief.askable_pods import askable_pod_lines

pytestmark = pytest.mark.unit

PERSON = uuid4()
HERE = uuid4()
SHARED_ONLY = uuid4()
PERSON_ONLY = uuid4()
BOTH = uuid4()


class _Uow:
    pass


class _UowFactory:
    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> _UowFactory:
        return self

    async def __aenter__(self) -> _Uow:
        self.opened += 1
        return _Uow()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Repository:
    """What ``list_askable_pods`` answers: one pod each way, and one both."""

    def __init__(self, uow: _Uow) -> None:
        del uow

    async def list_askable_pods(self, *, pod_id: UUID, user_id: UUID) -> list[Teammate]:
        del pod_id, user_id
        return [
            Teammate(
                pod_id=SHARED_ONLY,
                name="Billing",
                description=None,
                through_you=False,
                connected=True,
            ),
            Teammate(pod_id=PERSON_ONLY, name="Legal", description=None),
            Teammate(
                pod_id=BOTH,
                name="Ops",
                description="Runs the warehouse.",
                through_you=True,
                connected=True,
            ),
        ]


def _conversation(
    *,
    type: ConversationType = ConversationType.CHAT,
    metadata: dict[str, object] | None = None,
    origin: ConversationOrigin = ConversationOrigin.CHAT,
) -> Conversation:
    return Conversation(
        user_id=PERSON, pod_id=HERE, type=type, metadata=metadata, origin=origin
    )


async def _lines(conversation: Conversation, factory: _UowFactory | None = None):
    return await askable_pod_lines(
        factory or _UowFactory(),
        _Repository,
        conversation=conversation,
        pod_id=HERE,
        user_id=PERSON,
    )


async def test_the_persons_own_chat_lists_every_pod_by_every_way():
    brief = "\n".join(await _lines(_conversation()))

    assert "## Other pods you can ask" in brief
    assert f"Billing [connected] (teammate: {SHARED_ONLY})" in brief
    assert f"Legal [through the person] (teammate: {PERSON_ONLY})" in brief
    assert f"[through the person, connected] (teammate: {BOTH})" in brief


async def test_a_task_lists_only_connected_pods_and_only_as_connected():
    brief = "\n".join(await _lines(_conversation(type=ConversationType.TASK)))

    assert str(SHARED_ONLY) in brief
    assert str(PERSON_ONLY) not in brief
    assert f"Ops — Runs the warehouse. [connected] (teammate: {BOTH})" in brief
    pod_lines = [line for line in brief.splitlines() if "(teammate:" in line]
    assert all("through the person" not in line for line in pod_lines)


async def test_a_scheduled_chat_is_a_task_for_this_purpose():
    brief = "\n".join(await _lines(_conversation(origin=ConversationOrigin.SCHEDULE)))

    assert str(PERSON_ONLY) not in brief
    assert str(SHARED_ONLY) in brief


@pytest.mark.parametrize("kind", ["CHANNEL", "EMAIL"])
async def test_a_thread_others_read_lists_nothing_and_reads_nothing(kind):
    factory = _UowFactory()

    lines = await _lines(_conversation(metadata={"conversation_kind": kind}), factory)

    assert lines == []
    assert factory.opened == 0


async def test_a_direct_message_is_the_persons_own():
    lines = await _lines(_conversation(metadata={"conversation_kind": "DM"}))

    assert any(str(PERSON_ONLY) in line for line in lines)
