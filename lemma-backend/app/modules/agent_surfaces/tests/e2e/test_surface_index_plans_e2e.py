"""What the planner actually does with the hot reads of an inbound message.

Every index these reads need is named by the query that needs it, in a comment
beside its declaration. A comment is a claim, and this is the check:
`EXPLAIN (ANALYZE, FORMAT JSON)` the reads that run on every inbound message
and assert the shape of the plan.

Plan shape rather than statement counts, and rather than timings. A statement
count cannot tell a constant-cost read from one that scans a table growing per
chat -- the count is 1 either way. Timings on a laptop under Docker measure the
laptop. What does not lie is which node the planner chose, and whether the rows
it touched move when rows are added that the query is not for.

``ix_channel_sources_chat``
    The places hearing one chat: which of the deployment's surfaces a group
    message is for, and the group a reply in a thread belongs to.

``ix_channel_sources_person``
    One person's private chats: where a stranger who wrote before is answered,
    which surface a returning person's chat continues on, and where to reach a
    member.

``ix_agent_surface_routing``
    Every routing read leads with `(surface_type, status)`.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select, text

from app.modules.pod.infrastructure.models.pod_models import Pod

from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelSourceModel,
)

pytestmark = pytest.mark.e2e

_PLATFORM = SurfacePlatform.TELEGRAM.value


class _Uow:
    def __init__(self, session):
        self.session = session


@pytest.fixture
async def pod_agent_id(db_session, test_pod):
    return (
        await db_session.execute(
            select(AgentModel.id)
            .where(AgentModel.pod_id == UUID(str(test_pod["id"])))
            .limit(1)
        )
    ).scalar_one()


async def _surface(db_session, pod_id, agent_id, platform: str) -> AgentSurface:
    """One ACTIVE surface, on an agent of its own.

    An agent holds one surface per platform (`uq_agent_surface_agent_type`), so
    several surfaces of one platform means several agents.
    """
    template = await db_session.get(AgentModel, agent_id)
    sibling = AgentModel(
        id=uuid7(),
        pod_id=template.pod_id,
        user_id=template.user_id,
        name=f"agent-{uuid4().hex[:8]}",
        visibility=template.visibility,
        instruction="",
        toolsets=[],
        kind="USER",
    )
    db_session.add(sibling)
    surface = AgentSurface(
        id=uuid7(),
        pod_id=UUID(str(pod_id)),
        organization_id=await db_session.scalar(
            select(Pod.organization_id).where(Pod.id == UUID(str(pod_id)))
        ),
        agent_id=sibling.id,
        name=f"{platform.lower()}-{uuid4().hex[:8]}",
        surface_type=platform,
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
        status=AgentSurfaceStatus.ACTIVE.value,
    )
    db_session.add(surface)
    await db_session.commit()
    return surface


async def _a_channel(db_session, pod_id) -> UUID:
    """A channel the places can belong to. Which kind does not matter here:
    neither read is keyed on it."""
    channel = ChannelModel(id=uuid7(), pod_id=UUID(str(pod_id)), kind="GROUP")
    db_session.add(channel)
    await db_session.commit()
    return channel.id


def _place(surface: AgentSurface, channel_id: UUID, *, chat=None, person=None):
    return ChannelSourceModel(
        id=uuid7(),
        channel_id=channel_id,
        pod_id=surface.pod_id,
        surface_id=surface.id,
        platform=_PLATFORM,
        kind="PRIVATE" if person else "SHARED",
        external_channel_id=chat,
        external_user_id=person,
    )


async def _seed_places(
    db_session, surface: AgentSurface, channel_id: UUID, count: int, *, prefix: str
):
    """`count` places on one surface: a group chat and a person's chat each."""
    for index in range(count):
        db_session.add(_place(surface, channel_id, chat=f"{prefix}-chat-{index}"))
        db_session.add(_place(surface, channel_id, person=f"{prefix}-user-{index}"))
    await db_session.commit()


async def _plan(db_session, statement: str, params: dict) -> dict:
    """The executed plan, as JSON. ANALYZE so the row counts are real."""
    rows = await db_session.execute(
        text(f"EXPLAIN (ANALYZE, FORMAT JSON) {statement}"), params
    )
    payload = rows.scalar_one()
    return (json.loads(payload) if isinstance(payload, str) else payload)[0]["Plan"]


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _index_names(plan: dict) -> set[str]:
    return {node["Index Name"] for node in _nodes(plan) if "Index Name" in node}


def _rows_read(plan: dict, relation: str) -> int:
    """Rows the scan on one relation actually touched, loops included.

    Emitted *and* rejected. `Actual Rows` alone counts only what a node handed
    upwards, which is the wrong number for this question: a scan that inspects
    two hundred links and rejects all of them reports zero, exactly like an
    index lookup that visited one. `Rows Removed by Filter` is the part that
    grows with the deployment, so it is the part worth holding still.
    """
    return sum(
        (int(node.get("Actual Rows", 0)) + int(node.get("Rows Removed by Filter", 0)))
        * int(node.get("Actual Loops", 1))
        for node in _nodes(plan)
        if node.get("Relation Name") == relation
    )


#: What one lookup may touch, however many other chats exist. It finds a
#: single place; anything above a handful means the index was not used.
_ROW_CEILING = 5

_CHAT_SQL = """
SELECT id FROM channel_sources
WHERE platform = :platform
  AND external_channel_id = :chat
  AND external_user_id IS NULL
ORDER BY created_at
LIMIT 50
"""

_PERSON_SQL = """
SELECT id FROM channel_sources
WHERE platform = :platform
  AND external_user_id = :person
  AND kind = 'PRIVATE'
  AND surface_id IN (:surface)
ORDER BY coalesce(last_inbound_at, updated_at) DESC
LIMIT 1
"""


async def _held_still(db_session, test_pod, pod_agent_id, *, sql, index, under_test):
    """Plan ``sql`` at a few places and at hundreds: the index is used, and
    the rows it touches do not grow with other people's chats."""
    surface = await _surface(db_session, test_pod["id"], pod_agent_id, _PLATFORM)
    channel_id = await _a_channel(db_session, test_pod["id"])
    params = {
        "platform": _PLATFORM,
        "chat": "the-chat-under-test",
        "person": "the-person-under-test",
        "surface": surface.id,
    }
    # The place under test has to exist, or both plans emit nothing and the
    # comparison below is two zeros.
    db_session.add(_place(surface, channel_id, **under_test(params)))
    await db_session.commit()

    await _seed_places(db_session, surface, channel_id, 5, prefix="few")
    await db_session.execute(text("ANALYZE channel_sources"))
    few = await _plan(db_session, sql, params)

    await _seed_places(db_session, surface, channel_id, 200, prefix="many")
    await db_session.execute(text("ANALYZE channel_sources"))
    many = await _plan(db_session, sql, params)

    assert index in _index_names(many), (
        f"the read fell back to another plan: {_index_names(many)}"
    )
    read_few = _rows_read(few, "channel_sources")
    read_many = _rows_read(many, "channel_sources")
    assert read_few >= 1, (
        f"the place under test was not found, so this measured nothing: {read_few}"
    )
    # Not equality. At a dozen places a sequential scan is genuinely the
    # cheaper plan and Postgres takes it; at hundreds it switches to the index
    # and touches one. The property is that the work does not *grow* with other
    # people's chats.
    assert read_many <= read_few, (
        f"the read grew with other chats: {read_few} rows, then {read_many}"
    )
    assert read_many <= _ROW_CEILING, f"the read touched {read_many} rows"


async def test_the_places_hearing_a_chat_are_read_by_the_chat_index(
    test_pod, pod_agent_id, db_session
):
    await _held_still(
        db_session,
        test_pod,
        pod_agent_id,
        sql=_CHAT_SQL,
        index="ix_channel_sources_chat",
        under_test=lambda params: {"chat": params["chat"]},
    )


async def test_a_persons_private_places_are_read_by_the_person_index(
    test_pod, pod_agent_id, db_session
):
    await _held_still(
        db_session,
        test_pod,
        pod_agent_id,
        sql=_PERSON_SQL,
        index="ix_channel_sources_person",
        under_test=lambda params: {"person": params["person"]},
    )


async def test_the_routing_index_is_exactly_the_routing_predicate(db_session):
    """The index definition, checked against the query it is named for.

    Not a plan assertion, and the reason is worth stating. On a test database
    holding a handful of surfaces a sequential scan is genuinely the right plan,
    so EXPLAIN would report one however good the index is -- and this suite runs
    in a session pool that hands out a different connection per statement, so
    `SET enable_seqscan = off` does not survive to the EXPLAIN either. Faking
    either would produce a test that passes for a reason unrelated to the claim.

    What is asserted is the claim itself: that this index is the two columns
    `active_surfaces_of_type` filters on, in that order, and nothing else. It
    fails if someone widens it back to carry `(created_at, id)` -- see the note
    beside its declaration, where that was measured and rejected -- or reorders
    it so `surface_type` stops being the leading column the dropped standalone
    index was folded into.
    """
    definition = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes"
                " WHERE indexname = 'ix_agent_surface_routing'"
            )
        )
    ).scalar_one_or_none()

    assert definition is not None, "the routing index is missing"
    assert definition.endswith("USING btree (surface_type, status)"), definition
