"""A run's background is the group's timeline, read whenever its prompt is built.

A run keeps which lines of the group it was handed, never what they said, so
the timeline stays the only record of it: a line edited on the platform reads
as it says now in the next prompt, and a contact forgotten leaves no copy of
their words in anybody else's conversation. Against the pod's own log of a
Telegram group, with a scripted model whose every request is recorded.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.line_changes import apply_line_changes
from app.modules.agent_surfaces.tests.e2e.group_timeline import (
    a_stranger,
    say,
    timeline,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    record_model_requests,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.test_outsider_isolation_e2e import (
    _group_with_owner,
    _stranger_asks,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    GROUP_CHAT,
)
from app.modules.channels.contracts import Sender
from app.modules.channels.contracts.backgrounds import BACKGROUND_KEY
from app.modules.contacts.services.forget import forget_contact

pytestmark = pytest.mark.e2e

TOM_TELEGRAM_ID = "555001"


def _carried(seen: list[dict], since: int = 0) -> str:
    return " ".join(request["text"] for request in seen[since:])


async def _tom_says(db_session: AsyncSession, group, words: str, *, message_id: int):
    tom = await a_stranger(db_session, group, telegram_id=TOM_TELEGRAM_ID, name="Tom")
    assert tom.contact_id is not None
    await say(
        db_session,
        group,
        words,
        sender=Sender.contact(tom.contact_id, display_name="Tom"),
        external_message_id=str(message_id),
    )
    await db_session.commit()
    return tom.contact_id


async def _stored_words(pod_id: str, words: str) -> int:
    """How many of the pod's conversation messages hold these words, in what
    they say or in what they keep about themselves."""
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM agent_messages m "
                    "JOIN agent_conversations c ON c.id = m.conversation_id "
                    "WHERE c.pod_id = :pod AND (m.text LIKE :words "
                    "OR m.message_metadata::text LIKE :words)"
                ),
                {"pod": UUID(pod_id), "words": f"%{words}%"},
            )
            or 0
        )


def _edit(*, message_id: int, chat: int, words: str) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(
        source="telegram",
        payload={
            "update_id": 9000 + message_id,
            "edited_message": {
                "message_id": message_id,
                "from": {
                    "id": int(TOM_TELEGRAM_ID),
                    "is_bot": False,
                    "first_name": "Tom",
                },
                "chat": {"id": chat, "type": "supergroup", "title": "Launch crew"},
                "date": 1700000100,
                "edit_date": 1700000200,
                "text": words,
            },
        },
        headers={},
    )


async def _platform_says(db_session: AsyncSession, request) -> int:
    uow = SqlAlchemyUnitOfWork(db_session)
    changed = await apply_line_changes(
        uow, request, adapters=SurfacePlatformAdapterRegistry()
    )
    await uow.commit()
    return changed


async def test_a_line_edited_on_the_platform_reads_as_edited_in_the_next_prompt(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, _ = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    await _tom_says(db_session, group, "We ship on Friday.", message_id=41)
    seen = record_model_requests(monkeypatch)

    first = await _stranger_asks(
        db_session, text="when do we ship?", message_id=130, script=[script_text("Ok")]
    )
    assert "We ship on Friday." in _carried(seen)
    # What the run keeps is which lines, not what they said.
    messages = await _messages_for_conversation(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=str(first.conversation_id),
    )
    asked = [message for message in messages if message.get("role") == "user"][0]
    kept = json.dumps(asked["metadata"])
    assert BACKGROUND_KEY in asked["metadata"]
    assert "ship on Friday" not in kept and "channel_context" not in kept

    # Tom's edit in another group, of a message with the same number there,
    # is another message: nothing here changes.
    elsewhere = _edit(message_id=41, chat=GROUP_CHAT - 1, words="Never.")
    assert await _platform_says(db_session, elsewhere) == 0
    edit = _edit(message_id=41, chat=GROUP_CHAT, words="We ship on Monday.")
    assert await _platform_says(db_session, edit) == 1
    said = [line.text for line in await timeline(db_session, group)]
    assert "We ship on Monday." in said and "We ship on Friday." not in said

    before = len(seen)
    await _stranger_asks(
        db_session, text="and now?", message_id=131, script=[script_text("Ok")]
    )
    # The first question's background, replayed in the second run, reads the
    # line as it says now.
    now = _carried(seen, before)
    assert "We ship on Monday." in now
    assert "We ship on Friday." not in now


async def test_a_forgotten_contacts_words_are_in_nobody_elses_conversation(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    """Another stranger's run in the group was shown what Tom said. Forgetting
    Tom empties his lines; nothing of what he said survives in that run's
    conversation, nor reaches its next prompt."""
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    secret = "TOM-PRIVATE-WORDS my number is 555-0199"
    tom = await _tom_says(db_session, group, secret, message_id=42)
    seen = record_model_requests(monkeypatch)
    await _stranger_asks(
        db_session, text="who called?", message_id=140, script=[script_text("Tom.")]
    )
    assert secret in _carried(seen), "the other stranger's run was shown it"

    forgotten = await forget_contact(
        SessionUnitOfWorkFactory(async_session_maker),
        pod_id=UUID(scenario.pod_id),
        contact_id=tom,
        forgotten_by=owner,
    )
    assert forgotten is not None

    assert await _stored_words(scenario.pod_id, "TOM-PRIVATE-WORDS") == 0
    before = len(seen)
    await _stranger_asks(
        db_session, text="anyone else?", message_id=141, script=[script_text("No.")]
    )
    assert "TOM-PRIVATE-WORDS" not in _carried(seen, before)
