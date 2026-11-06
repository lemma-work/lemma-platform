"""The listing rule in SQL says what the Python rule says, for every shape.

A day's count across thousands of conversations reads the origin in Postgres
(``infrastructure/db/recent_conversations.listed_origin_column``) so as not to
load them; one conversation in hand reads it in Python
(``listed_origin.listed_origin_of``). The two are written separately, so they
are run here over the same stored shapes and must agree.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.listed_origin import listed_origin_of
from app.modules.agent.infrastructure.db.recent_conversations import (
    listed_origin_column,
)
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.tests.unit.test_listed_origin import SHAPES

pytestmark = [pytest.mark.e2e]


async def test_the_sql_origin_agrees_with_the_python_one(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Origins {uuid4().hex[:8]}",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id, user_id = UUID(pod.json()["id"]), UUID(fixed_test_user["id"])

    stored: dict[UUID, tuple] = {}
    async with async_session_maker() as session:
        for origin, conversation_type, metadata, _ in SHAPES:
            conversation = ConversationModel(
                user_id=user_id,
                pod_id=pod_id,
                origin=origin,
                conversation_type=conversation_type,
                conversation_metadata=metadata,
            )
            session.add(conversation)
            await session.flush()
            stored[conversation.id] = (origin, conversation_type, metadata)
        await session.commit()

        rows = await session.execute(
            select(ConversationModel.id, listed_origin_column()).where(
                ConversationModel.id.in_(list(stored))
            )
        )
        read_in_sql = dict(rows.tuples().all())

    assert len(read_in_sql) == len(SHAPES)
    for conversation_id, shape in stored.items():
        assert read_in_sql[conversation_id] == listed_origin_of(*shape).value, shape
