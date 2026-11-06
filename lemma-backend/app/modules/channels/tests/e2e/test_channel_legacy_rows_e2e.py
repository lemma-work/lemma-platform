"""E2E for rows written before the channels stopped writing them.

Nothing asks a contact a question any more, but a question an older release
asked one is still a row until the migration that drops the column -- and the
timeline, the line it hangs from and the inbox read it as it is.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import NewLine, Principal, Sender, post
from app.modules.channels.domain.entities import QuestionKind
from app.modules.channels.infrastructure.questions import NewQuestion
from app.modules.channels.tests.e2e.channel_pod import Pod, an_asker, contact_place

pytestmark = pytest.mark.e2e


async def test_a_question_an_older_release_put_to_a_contact_still_reads(
    pod: Pod, db_session: AsyncSession
):
    source, contact_id = await contact_place(db_session, pod)
    owner = UUID(pod.owner["id"])
    asked = await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=source.channel_id,
        line=NewLine(sender=Sender.member(owner), text="Which order?"),
        question=NewQuestion(
            kind=QuestionKind.ASK,
            ref="e2e:legacy-contact-question",
            asker=await an_asker(UUID(pod.id), owner),
            question={"prompt": "Which order?"},
            awaits=Principal.member(owner),
        ),
    )
    assert asked is not None
    # As an older release wrote it: waiting on the contact.
    await db_session.execute(
        text(
            "UPDATE channel_questions SET awaits_user_id = NULL,"
            " awaits_contact_id = :contact WHERE message_id = :line"
        ),
        {"contact": contact_id, "line": asked.id},
    )
    await db_session.commit()

    owner_client = pod.client()
    lines = await owner_client.get(f"{pod.base}/{source.channel_id}/messages")
    assert lines.status_code == 200, lines.text
    [line] = [item for item in lines.json()["items"] if item["id"] == str(asked.id)]
    assert line["question"]["awaits"]["kind"] == "CONTACT", line["question"]
    assert line["question"]["awaits"]["id"] == str(contact_id)
    inbox = await owner_client.get(f"/pods/{pod.id}/inbox")
    assert inbox.status_code == 200, inbox.text
