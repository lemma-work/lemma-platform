"""The two queries the notification journey rests on, against a real PostgreSQL.

`tests/unit/test_notification_journey.py` follows the whole path over in-memory
stand-ins for the link and notification repositories. Those stand-ins implement
the behaviour the real ones are supposed to have, which is exactly why the SQL
needs its own test: a fake that agrees with the design certifies nothing about a
`WHERE` clause that does not.

* ``conversation_holds_notification`` -- is this conversation where a
  notification's answer is expected? It decides whether a reply after a quiet
  day is cut into a new conversation, and whether a second notification opens
  one.
* ``count_open_from_origin_conversation`` -- how many asks an asking conversation
  is still owed. It decides whether the asker is woken.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (
    NotificationRepository,
)

pytestmark = pytest.mark.e2e


async def _notify(
    client: AsyncClient, pod_id: str, recipient: str, **overrides
) -> dict:
    payload = {
        "recipient": recipient,
        "title": "Offsite dates",
        "body": "Does the 14th work for you?",
        "background_instruction": "Record whether they can make the 14th.",
        "expects_response": True,
    }
    payload.update(overrides)
    response = await client.post(f"/pods/{pod_id}/notifications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _conversation(client: AsyncClient, pod_id: str, title: str) -> UUID:
    response = await client.post(
        f"/pods/{pod_id}/conversations", json={"title": title, "type": "CHAT"}
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _delivered(
    db_session: AsyncSession,
    notification_id: str,
    conversation_id: UUID,
    *,
    hours_ago: float = 0,
) -> None:
    """What a successful send records; the surface pipeline is not under test."""
    await db_session.execute(
        sql_text(
            "UPDATE notifications SET delivery_status = 'DELIVERED', "
            "delivery_conversation_id = :cid, delivered_at = :at "
            "WHERE id = :nid"
        ),
        {
            "cid": conversation_id,
            "nid": UUID(notification_id),
            "at": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        },
    )
    await db_session.commit()


def _links(db_session: AsyncSession) -> SurfaceConversationLinkRepository:
    return SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(db_session))


async def test_an_open_question_holds_its_conversation_however_old_it_is(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    conversation_id = await _conversation(authenticated_client, pod_id, "Notification")
    question = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )
    await _delivered(db_session, question["id"], conversation_id, hours_ago=70)

    repository = _links(db_session)

    assert await repository.conversation_holds_notification(conversation_id)
    assert not await repository.conversation_holds_notification(
        await _conversation(authenticated_client, pod_id, "Somewhere else")
    )


async def test_an_undelivered_notification_holds_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A row that never reached the person cannot be what they are answering."""
    pod_id = test_pod["id"]
    conversation_id = await _conversation(authenticated_client, pod_id, "Notification")
    question = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )
    await _delivered(db_session, question["id"], conversation_id)
    await db_session.execute(
        sql_text("UPDATE notifications SET delivery_status = 'FAILED' WHERE id = :id"),
        {"id": UUID(question["id"])},
    )
    await db_session.commit()

    assert not await _links(db_session).conversation_holds_notification(
        conversation_id, delivered_since=datetime.now(timezone.utc) - timedelta(hours=1)
    )


async def test_an_answered_or_informational_notification_holds_only_while_recent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    answered_in = await _conversation(authenticated_client, pod_id, "Answered")
    fyi_in = await _conversation(authenticated_client, pod_id, "Heads up")

    answered = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )
    await _delivered(db_session, answered["id"], answered_in, hours_ago=20)
    responded = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{answered['id']}/respond",
        json={"summary": "The 14th."},
    )
    assert responded.status_code == 200, responded.text

    fyi = await _notify(
        authenticated_client,
        pod_id,
        recipient=fixed_test_user["email"],
        title="Heads up",
        expects_response=False,
    )
    await _delivered(db_session, fyi["id"], fyi_in, hours_ago=20)

    repository = _links(db_session)
    now = datetime.now(timezone.utc)
    inside_window = now - timedelta(hours=24)
    outside_window = now - timedelta(hours=10)

    for conversation_id in (answered_in, fyi_in):
        # Nothing is owed, so with no window there is nothing to hold it.
        assert not await repository.conversation_holds_notification(conversation_id)
        # Delivered 20 hours ago: inside a 24 hour window, outside a 10 hour one.
        assert await repository.conversation_holds_notification(
            conversation_id, delivered_since=inside_window
        )
        assert not await repository.conversation_holds_notification(
            conversation_id, delivered_since=outside_window
        )


async def test_only_asks_keep_the_asking_conversation_waiting(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """An FYI is never answered, so it cannot be what the asker is owed."""
    pod_id = test_pod["id"]
    asking_conversation = await _conversation(authenticated_client, pod_id, "Asker")
    ask = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )
    fyi = await _notify(
        authenticated_client,
        pod_id,
        recipient=fixed_test_user["email"],
        title="Heads up",
        expects_response=False,
    )
    await db_session.execute(
        sql_text(
            "UPDATE notifications SET origin_kind = 'AGENT_RUN', "
            "origin_conversation_id = :cid WHERE id IN (:ask, :fyi)"
        ),
        {
            "cid": asking_conversation,
            "ask": UUID(ask["id"]),
            "fyi": UUID(fyi["id"]),
        },
    )
    await db_session.commit()
    repository = NotificationRepository(SqlAlchemyUnitOfWork(db_session))

    assert (
        await repository.count_open_from_origin_conversation(asking_conversation) == 1
    )

    # Closed directly rather than through `respond`: answering an AGENT_RUN ask
    # announces that its conversation is settled, which would start a real turn
    # in the conversation above -- and this test is about the count.
    await db_session.execute(
        sql_text("UPDATE notifications SET status = 'RESPONDED' WHERE id = :id"),
        {"id": UUID(ask["id"])},
    )
    await db_session.commit()

    assert (
        await repository.count_open_from_origin_conversation(asking_conversation) == 0
    )
