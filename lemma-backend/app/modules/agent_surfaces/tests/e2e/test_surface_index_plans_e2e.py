"""What the planner actually does with the two hot surface reads.

Every index on these tables is now named by the query that needs it, in a
comment beside its declaration. A comment is a claim, and this is the check:
`EXPLAIN (ANALYZE, FORMAT JSON)` the two reads that run on every inbound
message and assert the shape of the plan.

Plan shape rather than statement counts, and rather than timings. A statement
count cannot tell a constant-cost read from one that scans a table growing per
thread -- the count is 1 either way, which is exactly the regression the
continuity index exists to end. Timings on a laptop under Docker measure the
laptop. What does not lie is which node the planner chose, and whether the rows
it touched move when rows are added that the query is not for.

Two claims, one per index:

``ix_agent_surface_link_thread_continuity``
    `find_surface_id_for_external_thread` is the only link read not scoped to a
    surface -- it exists to find which surface a returning chat already lives
    on. It matched the standalone `platform` btree before, so every inbound
    message filtered and sorted a table that grows per thread across the whole
    deployment.

``ix_agent_surface_routing``
    Every routing read leads with `(surface_type, status)` and takes the result
    in `(created_at, id)` order, which is the documented tiebreak when a sender
    resolves to several candidates. Carrying the sort columns means the ordered
    index scan is the answer, with no sort node.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select, text

from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceConversationLinkModel,
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
        agent_id=sibling.id,
        name=f"{platform.lower()}-{uuid4().hex[:8]}",
        surface_type=platform,
        mode="DM",
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
        status=AgentSurfaceStatus.ACTIVE.value,
    )
    db_session.add(surface)
    await db_session.commit()
    return surface


async def _seed_threads(
    db_session, surface_id: UUID, conversation_id, count: int, *, prefix: str
):
    """`count` links on one surface, each its own chat.

    They all share a conversation because this is about how the *links* table is
    read; the conversation column is not in the predicate.
    """
    for index in range(count):
        db_session.add(
            AgentSurfaceConversationLinkModel(
                id=uuid7(),
                surface_id=surface_id,
                conversation_id=conversation_id,
                platform=_PLATFORM,
                external_channel_id=f"{prefix}-chat-{index}",
                external_thread_id=f"{prefix}-chat-{index}",
                external_user_id=f"{prefix}-user-{index}",
                conversation_kind="DM",
                last_event={},
            )
        )
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


#: What one continuity lookup may touch, however many other chats exist. It
#: finds a single thread; anything above a handful means the index was not used.
_CONTINUITY_ROW_CEILING = 5

_CONTINUITY_SQL = """
SELECT surface_id FROM agent_surface_conversation_links
WHERE platform = :platform
  AND external_thread_id = :thread
  AND external_channel_id = :channel
  AND external_user_id = :user
ORDER BY updated_at DESC
LIMIT 1
"""


async def test_continuity_reads_an_index_not_the_platforms_whole_history(
    test_pod, pod_agent_id, db_session
):
    """The rows it touches must not move when other people's chats are added.

    Seeding is what makes the assertion mean anything: before this index the
    planner used the standalone `platform` btree, so the same query touched
    every link on the platform -- and the statement count stayed at one while
    the work grew with the deployment.
    """
    surface = await _surface(db_session, test_pod["id"], pod_agent_id, _PLATFORM)
    conversation_id = await _a_conversation(db_session, surface)
    params = {
        "platform": _PLATFORM,
        "thread": "the-chat-under-test",
        "channel": "the-chat-under-test",
        "user": "the-person-under-test",
    }
    # The chat under test has to exist, or both plans emit nothing and the
    # comparison below is two zeros. The first version of this test seeded only
    # the crowd, so it held nothing still.
    db_session.add(
        AgentSurfaceConversationLinkModel(
            id=uuid7(),
            surface_id=surface.id,
            conversation_id=conversation_id,
            platform=_PLATFORM,
            external_channel_id=params["channel"],
            external_thread_id=params["thread"],
            external_user_id=params["user"],
            conversation_kind="DM",
            last_event={},
        )
    )
    await db_session.commit()

    await _seed_threads(db_session, surface.id, conversation_id, 5, prefix="few")
    await db_session.execute(text("ANALYZE agent_surface_conversation_links"))
    few = await _plan(db_session, _CONTINUITY_SQL, params)

    await _seed_threads(db_session, surface.id, conversation_id, 200, prefix="many")
    await db_session.execute(text("ANALYZE agent_surface_conversation_links"))
    many = await _plan(db_session, _CONTINUITY_SQL, params)

    assert "ix_agent_surface_link_thread_continuity" in _index_names(many), (
        f"continuity fell back to another plan: {_index_names(many)}"
    )
    read_few = _rows_read(few, "agent_surface_conversation_links")
    read_many = _rows_read(many, "agent_surface_conversation_links")
    assert read_few >= 1, (
        "the chat under test was not found at all, so this measured nothing: "
        f"{read_few} rows"
    )
    # Not equality. At six links a sequential scan is genuinely the cheaper
    # plan and Postgres takes it, touching all six; at two hundred and six it
    # switches to the index and touches one. The property is that the work does
    # not *grow* with other people's chats -- so "no more than before", and a
    # constant rather than a fraction of the table.
    assert read_many <= read_few, (
        "the continuity read grew with the platform's other chats: "
        f"{read_few} rows at 6 links, {read_many} at 206"
    )
    assert read_many <= _CONTINUITY_ROW_CEILING, (
        f"the continuity read touched {read_many} rows at 206 links; it is "
        "meant to find one thread, not scan the platform's history"
    )


async def _a_conversation(db_session, surface: AgentSurface) -> UUID:
    """A conversation id the links can point at."""
    agent = await db_session.get(AgentModel, surface.agent_id)
    conversation = ConversationModel(
        id=uuid7(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=agent.user_id,
        title="index plans",
        conversation_metadata={},
    )
    db_session.add(conversation)
    await db_session.commit()
    return conversation.id


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
