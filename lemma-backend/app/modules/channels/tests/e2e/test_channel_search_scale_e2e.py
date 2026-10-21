"""Search a pod of a hundred thousand lines: what it reads is bounded by the
page, not by how often the word was ever said.

A common word is in an eighth of the lines here, said over two years. Its
newest page is found by walking the pod's lines back from the newest
(``ix_channel_messages_pod_time``) until the page is full -- never by finding
every match and sorting them, which is what the search did before that
index: every one of the hundred thousand lines. A rare word, in a few lines
across the whole history, is found through its own posting list -- typed in
full or only its start. Rank and snippet come back with every hit, and the
newest is first.

Each line carries one of a few hundred other words too, as real lines do:
which plan is cheaper is the planner's call from the words' statistics, and
it sizes a word given by its start (``zebrac:*``) from the commonest
words it has counted -- with only a handful of them, it guesses, and guesses
a rare word common.

Plan shape is the check (``channel_scale``): the lines each search's
statements touch, explained again with their parameters. The p95 beside it
is a smoke bound against a plan that has blown up. Run with ``-s`` to read
the plans.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import text

from app.modules.channels.tests.e2e.channel_pod import Pod, group
from app.modules.channels.tests.e2e.channel_scale import p95_ms, shape_of

pytestmark = pytest.mark.e2e

LINES = 100_000
#: Ten minutes apart: the history spans about two years.
_SPACING_MINUTES = 10
COMMON, RARE = "bravo", "zebracorn"
#: In every eighth line, and every thousandth.
COMMON_EVERY, RARE_EVERY = 8, 1_000
PAGE = 30
#: What one search may read, a common word or a rare one: the lines walked
#: back to fill a page, or a rare word's matches -- far below every match of
#: a common word (an eighth of the lines) and every line.
LINES_CEILING = 2_000
BUDGET_MS = 750


async def _seed(db_session, pod: Pod) -> UUID:
    """A public group holding the whole history, said by the owner."""
    channel_id = UUID((await group(pod, title="history"))["id"])
    await db_session.execute(
        text(f"""
        INSERT INTO channel_messages
          (id, channel_id, pod_id, seq, sender_kind, sender_user_id, body,
           created_at, updated_at)
        SELECT gen_random_uuid(), :channel, :pod, s, 'MEMBER', :owner,
               'line ' || s || ' word' || (s % 300)
                 || CASE WHEN s % {COMMON_EVERY} = 0 THEN ' {COMMON}' ELSE '' END
                 || CASE WHEN s % {RARE_EVERY} = 7 THEN ' {RARE}' ELSE '' END,
               now() - ({LINES} - s) * interval '{_SPACING_MINUTES} minutes',
               now() - ({LINES} - s) * interval '{_SPACING_MINUTES} minutes'
        FROM generate_series(1, {LINES}) s
        """),
        {"channel": channel_id, "pod": UUID(pod.id), "owner": UUID(pod.owner["id"])},
    )
    await db_session.execute(
        text("UPDATE channels SET last_seq = :n WHERE id = :channel"),
        {"n": LINES, "channel": channel_id},
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE channel_messages"))
    await db_session.commit()
    return channel_id


def _search(pod: Pod, words: str):
    client = pod.client(pod.editor)

    async def read() -> dict:
        found = await client.get(f"/pods/{pod.id}/search", params={"q": words})
        assert found.status_code == 200, found.text
        return found.json()

    return read


async def test_search_reads_back_from_the_newest_not_every_match(
    pod: Pod, db_session, db_manager
):
    await _seed(db_session, pod)
    common, rare = _search(pod, COMMON), _search(pod, RARE)

    page = (await common())["items"]
    assert len(page) == PAGE
    said = [datetime.fromisoformat(item["message"]["created_at"]) for item in page]
    assert said == sorted(said, reverse=True), "not newest first"
    assert all(COMMON in item["message"]["text"] for item in page)
    assert all(item["rank"] > 0 and item["snippet"] for item in page)
    rare_page = (await rare())["items"]
    assert len(rare_page) == PAGE
    assert all(RARE in item["message"]["text"] for item in rare_page)

    for name, read, ceiling in (
        ("a common word", common, LINES_CEILING),
        ("a rare word", rare, LINES_CEILING),
        ("a rare word part typed", _search(pod, RARE[:6]), LINES_CEILING),
    ):
        shape = await shape_of(db_manager.engine, read)
        print(f"\n  {name}:\n{shape.describe()}")
        touched = shape.rows_read("channel_messages")
        assert touched <= ceiling, (
            f"{name}: touched {touched} lines, more than {ceiling}:\n"
            f"{shape.describe(verbose=True)}"
        )
        p95 = await p95_ms(read)
        print(f"  {name}: p95 {p95:.1f}ms (budget {BUDGET_MS}ms)")
        assert p95 < BUDGET_MS, f"{name}: p95 {p95:.1f}ms"
