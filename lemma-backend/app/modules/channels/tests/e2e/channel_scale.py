"""A pod at the size the channels' reads are budgeted for, and how to look at
what the planner does with them.

The pod: one channel of ``LONG_CHANNEL_LINES`` lines -- threads, mentions,
questions, a rare word and common ones -- and one member (the editor) in
``MEMBER_CHANNELS`` channels of every kind, with unread lines, mentions and
questions spread across them, beside channels they cannot see or never
joined. Written in bulk through SQL, because posting it through the API would
cost minutes and measure nothing the reads care about.

The reads are looked at the way the endpoint issues them: every channel
statement a request runs is captured with its parameters and explained again,
so a read changed in the repository is checked as changed, with nothing here
to keep in step with it.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

#: The long channel's length.
LONG_CHANNEL_LINES = 10_000
#: How many channels the editor is in, their own DIRECT among them.
MEMBER_CHANNELS = 500
#: Lines in every other channel.
SHORT_CHANNEL_LINES = 20
#: A word said in ten of the long channel's lines, and one in an eighth.
RARE_WORD = "zebracorn"
COMMON_WORD = "bravo"

#: The relations that grow with a pod's history, seeded here in the
#: proportion they hold anywhere real -- this pod a small part of each -- so
#: the planner's choice is the one it makes there: none of the budgeted reads
#: may scan one whole, or rescan one per row. Questions are left out: a
#: page's outer join to them is hashed while the table is small enough for
#: that to be cheaper than a probe per line, and a platform of questions large
#: enough to tip it costs more to seed than the read it would check.
GROWING = (
    "channel_messages",
    "channel_members",
    "channel_mentions",
    "channel_read_state",
)

# The pod's channels, numbered by how recently they were active; the member
# is in every one numbered below ``MEMBER_CHANNELS`` and in their own DIRECT.
#   1-250    public groups (1 is the long channel)
#   251-400  private groups
#   401-499  contacts' channels
#   500-599  public groups they never joined
#   600-699  private groups they are not in, and so cannot see
#   700-750  contacts' channels they never joined
_LAYOUT = """
CASE WHEN n BETWEEN 401 AND 499 OR n > 699 THEN 'CONTACT' ELSE 'GROUP' END
"""
_CHANNELS = 750


@dataclass(frozen=True, slots=True)
class ScalePod:
    pod_id: UUID
    member_id: UUID
    long_channel_id: UUID
    #: The long channel's thread with the most replies.
    long_thread_root_id: UUID


async def seed(
    session: AsyncSession, *, pod_id: UUID, member_id: UUID, others: list[UUID]
) -> ScalePod:
    """The pod above, for ``member_id``; ``others[0]`` writes every line and
    owns every group, the rest sit in half of them."""
    await _seed_platform(session)
    writer = others[0]
    params: dict[str, object] = {
        "pod": pod_id,
        "pod_key": str(pod_id),
        "member": member_id,
        "writer": writer,
    }
    await session.execute(
        text(f"""
        CREATE TEMP TABLE seed_channels ON COMMIT DROP AS
        SELECT n,
               md5(CAST(:pod_key AS text) || ':channel:' || n)::uuid AS id,
               {_LAYOUT} AS kind,
               CASE WHEN n <= 250 OR n BETWEEN 500 AND 599 THEN 'PUBLIC'
                    ELSE 'PRIVATE' END AS visibility,
               CASE WHEN {_LAYOUT} = 'CONTACT'
                    THEN md5(CAST(:pod_key AS text) || ':contact:' || n)::uuid END
                 AS contact_id,
               n < {MEMBER_CHANNELS} AS joined,
               CASE WHEN n = 1 THEN {LONG_CHANNEL_LINES}
                    ELSE {SHORT_CHANNEL_LINES} END AS lines,
               now() - n * interval '1 minute' AS last_line_at
        FROM generate_series(1, {_CHANNELS}) n
        """),
        params,
    )
    await session.execute(
        text("""
        INSERT INTO contacts (id, pod_id, display_name, created_at, updated_at)
        SELECT contact_id, :pod, 'Contact ' || n, now(), now()
        FROM seed_channels WHERE contact_id IS NOT NULL
        """),
        params,
    )
    await session.execute(
        text("""
        INSERT INTO channels
          (id, pod_id, kind, visibility, title, contact_id, created_by_user_id,
           last_seq, last_line_id, last_line_at, last_rev, changed_rev,
           created_at, updated_at)
        SELECT id, :pod, kind, visibility,
               CASE WHEN kind = 'GROUP' THEN 'group ' || n END, contact_id,
               CASE WHEN kind = 'GROUP' THEN CAST(:writer AS uuid) END,
               lines, md5(id::text || ':' || lines)::uuid, last_line_at, lines, 0,
               now(), now()
        FROM seed_channels
        """),
        params,
    )
    await _seed_lines(session, params)
    await _seed_seats(session, params, others)
    await _seed_attention(session, params)
    direct = await _direct_of(session, pod_id=pod_id, member_id=member_id)
    long_channel = await session.scalar(
        text("SELECT id FROM seed_channels WHERE n = 1")
    )
    await session.commit()
    for table in (
        "channels",
        "channel_members",
        "channel_messages",
        "channel_mentions",
        "channel_questions",
        "channel_read_state",
    ):
        await session.execute(text(f"ANALYZE {table}"))
    await session.commit()
    joined = await session.scalar(
        text(
            "SELECT count(*) FROM channel_members"
            " WHERE pod_id = :pod AND user_id = :member"
        ),
        params,
    )
    assert joined == MEMBER_CHANNELS, (
        f"the member is in {joined} channels, not {MEMBER_CHANNELS}; "
        f"their own channel: {direct}"
    )
    assert long_channel is not None
    return ScalePod(
        pod_id=pod_id,
        member_id=member_id,
        long_channel_id=long_channel,
        long_thread_root_id=_line_id(long_channel, 1),
    )


#: The rest of the platform, which the pod's reads must not pay for: other
#: pods' channels, each with lines, seats, mentions and questions.
_OTHER_PODS = 40
_OTHER_CHANNELS = 50
_OTHER_LINES = 40
_OTHER_SEATS = 16


async def _seed_platform(session: AsyncSession) -> None:
    """Every relation the reads touch, holding far more of other pods than of
    this one -- as it does anywhere but a test database, and the only way the
    planner's choice here is the choice it makes there.

    Fabricated pods and people: foreign keys are not under test, and
    ``truncate_all`` empties these tables before the next test.
    """
    await session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    await session.execute(
        text(f"""
        CREATE TEMP TABLE platform_channels ON COMMIT DROP AS
        SELECT md5('platform:channel:' || p || ':' || c)::uuid AS id,
               md5('platform:pod:' || p)::uuid AS pod_id, p, c,
               now() - (p * {_OTHER_CHANNELS} + c) * interval '1 second'
                 AS last_line_at
        FROM generate_series(1, {_OTHER_PODS}) p,
             generate_series(1, {_OTHER_CHANNELS}) c
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channels
          (id, pod_id, kind, visibility, title, last_seq, last_line_id,
           last_line_at, last_rev, changed_rev, created_at, updated_at)
        SELECT id, pod_id, 'GROUP',
               CASE WHEN c % 2 = 0 THEN 'PUBLIC' ELSE 'PRIVATE' END,
               'platform ' || c, {_OTHER_LINES},
               md5(id::text || ':' || {_OTHER_LINES})::uuid, last_line_at,
               {_OTHER_LINES}, 0, now(), now()
        FROM platform_channels
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channel_messages
          (id, channel_id, pod_id, seq, sender_kind, sender_user_id, body,
           created_at, updated_at)
        SELECT md5(c.id::text || ':' || s)::uuid, c.id, c.pod_id, s, 'MEMBER',
               md5('platform:user:' || c.p || ':' || s % {_OTHER_SEATS})::uuid,
               'line ' || s || ' '
                 || (ARRAY['alpha','{COMMON_WORD}','charlie','delta',
                           'echo','foxtrot','golf','hotel'])[1 + s % 8]
                 || CASE WHEN s % 50 = 7 THEN ' {RARE_WORD}' ELSE '' END,
               c.last_line_at - ({_OTHER_LINES} - s) * interval '1 second',
               c.last_line_at - ({_OTHER_LINES} - s) * interval '1 second'
        FROM platform_channels c, generate_series(1, {_OTHER_LINES}) s
        """)
    )
    await _seed_changes(session, "platform_channels")
    await session.execute(
        text(f"""
        INSERT INTO channel_members
          (id, channel_id, pod_id, user_id, role, notify, seated_by,
           created_at, updated_at)
        SELECT gen_random_uuid(), c.id, c.pod_id,
               md5('platform:user:' || c.p || ':' || k)::uuid, 'member', 'ALL',
               'ADDED', now(), now()
        FROM platform_channels c, generate_series(0, {_OTHER_SEATS} - 1) k
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channel_read_state
          (user_id, channel_id, pod_id, last_read_seq, updated_at)
        SELECT md5('platform:user:' || c.p || ':' || k)::uuid, c.id, c.pod_id,
               {_OTHER_LINES} - k, now()
        FROM platform_channels c, generate_series(0, {_OTHER_SEATS} - 1) k
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channel_mentions
          (id, message_id, channel_id, pod_id, seq, user_id, created_at, updated_at)
        SELECT gen_random_uuid(), m.id, m.channel_id, m.pod_id, m.seq,
               md5('platform:user:' || c.p || ':' || m.seq % {_OTHER_SEATS})::uuid,
               m.created_at, m.created_at
        FROM channel_messages m JOIN platform_channels c ON c.id = m.channel_id
        """)
    )
    # Read where it was said, as reading the timeline marks it.
    await session.execute(
        text(f"""
        UPDATE channel_mentions m SET read_at = m.created_at
        FROM platform_channels c
        WHERE c.id = m.channel_id
          AND m.seq <= {_OTHER_LINES} - m.seq % {_OTHER_SEATS}
        """)
    )
    await session.execute(
        text("""
        INSERT INTO channel_questions
          (message_id, channel_id, pod_id, ref, asker, asker_key, kind, state,
           question, awaits_user_id, created_at)
        SELECT m.id, m.channel_id, m.pod_id, 'msg:' || m.id, 'MESSAGE',
               m.id::text, 'ASK',
               CASE WHEN m.seq = 30 THEN 'OPEN' ELSE 'ANSWERED' END,
               '{"text": "Ship it?"}'::jsonb, m.sender_user_id, m.created_at
        FROM channel_messages m JOIN platform_channels c ON c.id = m.channel_id
        WHERE m.seq IN (10, 30)
        """)
    )
    await session.commit()


def channel_id(pod_id: UUID, n: int) -> UUID:
    """The id ``seed`` gives the pod's channel ``n``."""
    return UUID(hashlib.md5(f"{pod_id}:channel:{n}".encode()).hexdigest())


def _line_id(channel_id: UUID, seq: int) -> UUID:
    """The id ``seed`` gives line ``seq`` of a channel."""
    return UUID(hashlib.md5(f"{channel_id}:{seq}".encode()).hexdigest())


async def _seed_lines(session: AsyncSession, params: dict[str, object]) -> None:
    """Every channel's lines. In the long one, every tenth line from the fifth
    replies to its first line -- one long thread -- and every twentieth from
    the twenty-first to the line before it; a contact says every other line of
    their own channel."""
    await session.execute(
        text(f"""
        INSERT INTO channel_messages
          (id, channel_id, pod_id, seq, sender_kind, sender_user_id,
           sender_contact_id, body, thread_root_id, created_at, updated_at)
        SELECT md5(c.id::text || ':' || s)::uuid, c.id, :pod, s,
               CASE WHEN c.contact_id IS NOT NULL AND s % 2 = 1
                    THEN 'CONTACT' ELSE 'MEMBER' END,
               CASE WHEN c.contact_id IS NOT NULL AND s % 2 = 1
                    THEN NULL ELSE CAST(:writer AS uuid) END,
               CASE WHEN s % 2 = 1 THEN c.contact_id END,
               'line ' || s || ' '
                 || (ARRAY['alpha','{COMMON_WORD}','charlie','delta',
                           'echo','foxtrot','golf','hotel'])[1 + s % 8]
                 || CASE WHEN s % 1000 = 7 THEN ' {RARE_WORD}' ELSE '' END,
               CASE WHEN c.n = 1 AND s % 10 = 5
                      THEN md5(c.id::text || ':' || 1)::uuid
                    WHEN c.n = 1 AND s % 20 = 1 AND s > 1
                      THEN md5(c.id::text || ':' || (s - 1))::uuid END,
               c.last_line_at - (c.lines - s) * interval '1 second',
               c.last_line_at - (c.lines - s) * interval '1 second'
        FROM seed_channels c, generate_series(1, c.lines) s
        """),
        params,
    )
    await _seed_changes(session, "seed_channels")


async def _seed_changes(session: AsyncSession, seeded: str) -> None:
    """Each seeded line as its channel's change of the same number (the
    channel's ``last_rev`` is its line count), and its thread's count."""
    await session.execute(
        text(f"""
        UPDATE channels c SET replies = r.replies
        FROM (
          SELECT m.channel_id, count(*) AS replies
          FROM channel_messages m JOIN {seeded} s ON s.id = m.channel_id
          WHERE m.thread_root_id IS NOT NULL
          GROUP BY m.channel_id
        ) r
        WHERE c.id = r.channel_id
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channel_changes (channel_id, rev, message_id, pod_id, at)
        SELECT m.channel_id, m.seq, m.id, m.pod_id, m.created_at
        FROM channel_messages m JOIN {seeded} c ON c.id = m.channel_id
        """)
    )
    await session.execute(
        text(f"""
        INSERT INTO channel_threads (root_id, channel_id, reply_count, last_reply_at)
        SELECT m.thread_root_id, m.channel_id, count(*), max(m.created_at)
        FROM channel_messages m JOIN {seeded} c ON c.id = m.channel_id
        WHERE m.thread_root_id IS NOT NULL
        GROUP BY m.thread_root_id, m.channel_id
        """)
    )


async def _seed_seats(
    session: AsyncSession, params: dict[str, object], others: list[UUID]
) -> None:
    """Who is in what. The member is in every channel below ``MEMBER_CHANNELS``
    and hears nothing from every tenth; the writer is in every group, a
    contact in their own channel, the other members in every other group."""
    await session.execute(
        text("""
        INSERT INTO channel_members
          (id, channel_id, pod_id, user_id, contact_id, role, notify,
           seated_by, created_at, updated_at)
        SELECT gen_random_uuid(), id, CAST(:pod AS uuid), CAST(:member AS uuid),
               CAST(NULL AS uuid), 'member',
               CASE WHEN n % 10 = 0 THEN 'NONE' ELSE 'ALL' END, 'JOINED',
               now(), now()
        FROM seed_channels WHERE joined
        UNION ALL
        SELECT gen_random_uuid(), id, CAST(:pod AS uuid), CAST(:writer AS uuid),
               NULL, 'owner', 'ALL', 'CREATOR', now(), now()
        FROM seed_channels WHERE kind = 'GROUP'
        UNION ALL
        SELECT gen_random_uuid(), id, CAST(:pod AS uuid), NULL, contact_id,
               'guest', 'ALL', 'CREATOR', now(), now()
        FROM seed_channels WHERE contact_id IS NOT NULL
        """),
        params,
    )
    for other in others[1:]:
        await session.execute(
            text("""
            INSERT INTO channel_members
              (id, channel_id, pod_id, user_id, role, notify, seated_by,
               created_at, updated_at)
            SELECT gen_random_uuid(), id, CAST(:pod AS uuid), CAST(:other AS uuid),
                   'member', 'ALL', 'ADDED', now(), now()
            FROM seed_channels WHERE kind = 'GROUP' AND n % 2 = 0
            """),
            {**params, "other": other},
        )


async def _seed_attention(session: AsyncSession, params: dict[str, object]) -> None:
    """What is new for the member, and what is not.

    They have read the long channel to its last hundred lines and every other
    channel to within a few, but never opened every eleventh. They are named
    in every tenth line -- a thousand times in the long channel, so a count
    that stops after the member's first thousand mentions in a page of
    channels never reaches the rest -- and the writer in the channels the
    member is not in. A question waits on them in every seventeenth channel
    and near the long channel's end; others they answered.
    """
    await session.execute(
        text("""
        INSERT INTO channel_read_state
          (user_id, channel_id, pod_id, last_read_seq, updated_at)
        SELECT :member, id, :pod,
               CASE WHEN n = 1 THEN lines - 100 ELSE lines - n % 6 END, now()
        FROM seed_channels WHERE joined AND n % 11 <> 0
        """),
        params,
    )
    await session.execute(
        text("""
        INSERT INTO channel_mentions
          (id, message_id, channel_id, pod_id, seq, user_id, created_at, updated_at)
        SELECT gen_random_uuid(), m.id, m.channel_id, m.pod_id, m.seq,
               CASE WHEN c.joined THEN CAST(:member AS uuid)
                    ELSE CAST(:writer AS uuid) END,
               m.created_at, m.created_at
        FROM channel_messages m JOIN seed_channels c ON c.id = m.channel_id
        WHERE m.thread_root_id IS NULL
          AND m.seq % 10 = 0
        """),
        params,
    )
    # The two updates below join ~100k rows this transaction just wrote.
    # Planned on the statistics an earlier test left behind they once took
    # over two minutes instead of ten seconds, and a seed killed mid-statement
    # by the test timeout left its transaction holding every later reset.
    # ANALYZE inside the transaction counts its own rows; temp tables are
    # never analyzed by autovacuum.
    for table in (
        "seed_channels",
        "channel_messages",
        "channel_mentions",
        "channel_read_state",
    ):
        await session.execute(text(f"ANALYZE {table}"))
    # What the member read is read: its replies counted, and the lines naming
    # them up to there -- as reading the timeline through the API leaves it.
    await session.execute(
        text("""
        UPDATE channel_read_state r SET read_replies = (
          SELECT count(*) FROM channel_messages m
          WHERE m.channel_id = r.channel_id AND m.seq <= r.last_read_seq
            AND m.thread_root_id IS NOT NULL
        )
        FROM seed_channels c WHERE c.id = r.channel_id
        """)
    )
    await session.execute(
        text("""
        UPDATE channel_mentions m SET read_at = m.created_at
        FROM channel_read_state r
        WHERE r.channel_id = m.channel_id AND r.user_id = m.user_id
          AND m.seq <= r.last_read_seq AND m.pod_id = :pod
        """),
        params,
    )
    await session.execute(
        text("""
        INSERT INTO channel_questions
          (message_id, channel_id, pod_id, ref, asker, asker_key, kind, state,
           question, awaits_user_id, created_at)
        SELECT m.id, m.channel_id, m.pod_id, 'msg:' || m.id, 'MESSAGE',
               m.id::text, 'ASK',
               CASE WHEN m.seq IN (18, 9990) THEN 'OPEN' ELSE 'ANSWERED' END,
               '{"text": "Ship it?", "choices": ["yes", "no"]}'::jsonb,
               CASE WHEN c.joined THEN CAST(:member AS uuid)
                    ELSE CAST(:writer AS uuid) END,
               m.created_at
        FROM channel_messages m JOIN seed_channels c ON c.id = m.channel_id
        WHERE (c.n % 17 = 0 AND m.seq = 18)
           OR (c.n % 13 = 0 AND m.seq = 12)
           OR (c.n = 1 AND m.seq IN (9000, 9500, 9990))
        """),
        params,
    )


async def _direct_of(session: AsyncSession, *, pod_id: UUID, member_id: UUID) -> UUID:
    """The member's own channel: made when they joined the pod, or now."""
    await session.execute(
        text("""
        INSERT INTO channels
          (id, pod_id, kind, visibility, member_user_id, created_at, updated_at)
        VALUES (gen_random_uuid(), :pod, 'DIRECT', 'PRIVATE', :member, now(), now())
        ON CONFLICT (member_user_id, pod_id) DO NOTHING
        """),
        {"pod": pod_id, "member": member_id},
    )
    direct = await session.scalar(
        text(
            "SELECT id FROM channels"
            " WHERE pod_id = :pod AND kind = 'DIRECT' AND member_user_id = :member"
        ),
        {"pod": pod_id, "member": member_id},
    )
    assert direct is not None
    await session.execute(
        text("""
        INSERT INTO channel_members
          (id, channel_id, pod_id, user_id, role, notify, seated_by,
           created_at, updated_at)
        VALUES (gen_random_uuid(), :channel, :pod, :member, 'owner', 'ALL',
                'CREATOR', now(), now())
        ON CONFLICT (channel_id, user_id) WHERE user_id IS NOT NULL DO NOTHING
        """),
        {"pod": pod_id, "member": member_id, "channel": direct},
    )
    return direct


# -- the statements a read issues ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Statement:
    sql: str
    parameters: tuple[object, ...]


@contextmanager
def captured_reads() -> Iterator[list[Statement]]:
    """Every ``SELECT`` naming a channel table, with its parameters, that runs
    while the block does -- on any engine, as ``query_counting`` listens."""
    found: list[Statement] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip()[:6].upper() == "SELECT" and "channel" in statement:
            found.append(Statement(statement, tuple(parameters or ())))

    event.listen(Engine, "before_cursor_execute", before)
    try:
        yield found
    finally:
        event.remove(Engine, "before_cursor_execute", before)


@dataclass(frozen=True, slots=True)
class Plan:
    sql: str
    root: dict[str, object]

    def nodes(self) -> Iterator[dict[str, object]]:
        def walk(node: dict[str, object]) -> Iterator[dict[str, object]]:
            yield node
            children = node.get("Plans", [])
            assert isinstance(children, list)
            for child in children:
                yield from walk(child)

        return walk(self.root)

    def seq_scans(self) -> list[str]:
        """Growing relations read whole -- by a node that ran: a plan may hold
        alternatives the executor never entered."""
        return [
            str(node["Relation Name"])
            for node in self.nodes()
            if node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") in GROWING
            and _int(node.get("Actual Loops", 0)) > 0
        ]

    def rescans(self) -> list[str]:
        """Nodes over a growing relation that run once per outer row and are
        not a probe of an index: the whole relation, or a materialized copy of
        it, read again for every row. Under a ``Gather`` each process runs its
        share once, so loops up to the processes there are not repeats."""
        found: list[str] = []

        def walk(node: dict[str, object], processes: int) -> None:
            if "Workers Launched" in node:
                processes = _int(node["Workers Launched"]) + 1
            loops = _int(node.get("Actual Loops", 1))
            if (
                loops > processes
                and node.get("Relation Name") in GROWING
                and node.get("Node Type") not in ("Index Scan", "Index Only Scan")
            ):
                found.append(f"{node['Node Type']} on {node['Relation Name']} x{loops}")
            children = node.get("Plans", [])
            assert isinstance(children, list)
            for child in children:
                walk(child, processes)

        walk(self.root, 1)
        return found

    def tree(self) -> str:
        """The plan, a node a line: what it read, through what, how often."""

        def walk(node: dict[str, object], depth: int) -> Iterator[str]:
            where = f" on {node['Relation Name']}" if "Relation Name" in node else ""
            index = f" using {node['Index Name']}" if "Index Name" in node else ""
            removed = _int(node.get("Rows Removed by Filter", 0))
            yield (
                f"{'  ' * depth}{node['Node Type']}{where}{index}"
                f" loops={node.get('Actual Loops')} rows={node.get('Actual Rows')}"
                f" (est {node.get('Plan Rows')})"
                + (f" removed={removed}" if removed else "")
            )
            children = node.get("Plans", [])
            assert isinstance(children, list)
            for child in children:
                yield from walk(child, depth + 1)

        return "\n".join(walk(self.root, 4))

    def rows_read(self, relation: str) -> int:
        """Rows the scans of ``relation`` touched, kept or thrown away, every
        loop counted."""
        return sum(
            (
                _int(node.get("Actual Rows", 0))
                + _int(node.get("Rows Removed by Filter", 0))
                + _int(node.get("Rows Removed by Index Recheck", 0))
            )
            * _int(node.get("Actual Loops", 1))
            for node in self.nodes()
            if node.get("Relation Name") == relation
        )


def _int(value: object) -> int:
    assert isinstance(value, int | float), value
    return int(value)


async def explain(engine: AsyncEngine, statements: list[Statement]) -> list[Plan]:
    """Each statement run again under ``EXPLAIN (ANALYZE, BUFFERS)``, with the
    parameters it was run with."""
    plans: list[Plan] = []
    async with engine.connect() as conn:
        for statement in statements:
            result = await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement.sql,
                statement.parameters,
            )
            payload = result.scalar_one()
            parsed = json.loads(payload) if isinstance(payload, str) else payload
            plans.append(Plan(statement.sql, parsed[0]["Plan"]))
        await conn.rollback()
    return plans


@dataclass(slots=True)
class Shape:
    """What a read's statements did, together."""

    plans: list[Plan]
    seq_scans: list[str] = field(default_factory=list)
    rescans: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for plan in self.plans:
            self.seq_scans += plan.seq_scans()
            self.rescans += plan.rescans()

    def rows_read(self, relation: str) -> int:
        return sum(plan.rows_read(relation) for plan in self.plans)

    def describe(self, *, verbose: bool = False) -> str:
        lines = []
        for plan in self.plans:
            touched = {
                relation: plan.rows_read(relation)
                for relation in GROWING
                if plan.rows_read(relation)
            }
            lines.append(
                f"    {' '.join(plan.sql.split())[:110]}\n"
                f"      read {touched} seq={plan.seq_scans()} rescans={plan.rescans()}"
            )
            if plan.seq_scans() or plan.rescans() or verbose:
                lines.append(f"      {' '.join(plan.sql.split())}\n{plan.tree()}")
        return "\n".join(lines)


async def shape_of(engine: AsyncEngine, read: Callable[[], Awaitable[object]]) -> Shape:
    """The plans of every channel statement ``read`` issues."""
    with captured_reads() as statements:
        await read()
    assert statements, "the read issued no channel statement at all"
    return Shape(await explain(engine, statements))


# -- the clock ---------------------------------------------------------------------

#: Requests before the measured ones: the first pays for caches and plans.
WARMUP = 3
#: Measured requests per read.
SAMPLES = 30


async def p95_ms(read: Callable[[], Awaitable[object]]) -> float:
    for _ in range(WARMUP):
        await read()
    samples: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        await read()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples[math.ceil(0.95 * len(samples)) - 1]
