"""The channels' hot reads hold their shape at the size they are budgeted for:
a channel ten thousand lines long, and a member in five hundred channels.

Plan shape is the check, and the clock is beside it. A read that scans a
channel's whole timeline, or a member's every mention, to answer a page is
fast on a test database and slow everywhere it matters; wall time on a shared
runner says more about the runner. So each read is asserted on the statements
it actually issues (``channel_scale.captured_reads``), explained again with
their parameters:

- no sequential scan of a relation that grows with history -- lines, seats,
  mentions;
- nothing over one entered again per outer row except a probe of an index;
- the lines it touches bounded by what it shows, not by the channel's length;

and then a p95 over ``channel_scale.SAMPLES`` requests through the API, under
a budget that is a smoke bound against a plan that has blown up rather than a
latency target -- each one says what the plan does that makes it enough. Run
with ``-s`` to read the p95s and every statement's plan.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text

from app.modules.channels.api.inbox_controller import INBOX_ITEMS
from app.modules.channels.infrastructure.inbox import UNREAD_CAP
from app.modules.channels.tests.e2e.channel_pod import Pod
from app.modules.channels.tests.e2e.channel_scale import (
    COMMON_WORD,
    LONG_CHANNEL_LINES,
    MEMBER_CHANNELS,
    RARE_WORD,
    ScalePod,
    Shape,
    channel_id,
    p95_ms,
    seed,
    shape_of,
)

pytestmark = pytest.mark.e2e

#: A page, as the timeline and the channel list ask for one (their default).
PAGE = 50
#: What the search endpoint returns by default.
SEARCH_PAGE = 30

#: A page of lines: one walk back along ``(channel_id, seq)`` from the newest
#: or from ``before_seq``, a probe of each line's question by its key, and the
#: page's reply counts by root from ``channel_threads``. A handful of index
#: ranges whatever the channel's length.
PAGE_BUDGET_MS = 500
#: A thread: its root by key, then one page of replies along
#: ``(thread_root_id, seq)``.
THREAD_BUDGET_MS = 500
#: Search in one channel: the text index finds the lines holding the word and
#: they are sorted for the newest page. A rare word costs its handful.
RARE_SEARCH_BUDGET_MS = 500
#: A common word costs every line holding it -- an eighth of the channel here
#: -- sorted once; never the channel itself.
COMMON_SEARCH_BUDGET_MS = 750
#: A page of channels: walked along ``(pod_id, last_line_at, id)``, the member's
#: seat probed for each private one, then a fixed number of reads for the whole
#: page -- seats, read numbers, one range of unread mentions per channel,
#: questions, places, last lines.
CHANNEL_LIST_BUDGET_MS = 750
#: The inbox: three counts and two short lists. Unread lines are each
#: channel's head against the member's read number, over their seats; mentions
#: and questions come from the member's own indexes; the lines shown are read
#: by key.
INBOX_BUDGET_MS = 750


@pytest.fixture
async def scale(pod: Pod, db_session) -> ScalePod:
    return await seed(
        db_session,
        pod_id=UUID(pod.id),
        member_id=UUID(pod.editor["id"]),
        others=[UUID(pod.owner["id"]), UUID(pod.user["id"]), UUID(pod.viewer["id"])],
    )


def _get(pod: Pod, url: str, **params: object):
    client = pod.client(pod.editor)

    async def read() -> dict:
        response = await client.get(url, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    return read


def _assert_shape(
    name: str,
    shape: Shape,
    *,
    lines_ceiling: int,
    channels_ceiling: int | None = None,
) -> None:
    print(f"\n  {name}:\n{shape.describe()}")
    assert not shape.seq_scans, (
        f"{name} scans {shape.seq_scans} whole:\n{shape.describe(verbose=True)}"
    )
    assert not shape.rescans, (
        f"{name} reads {shape.rescans} again for every row:\n"
        f"{shape.describe(verbose=True)}"
    )
    read = shape.rows_read("channel_messages")
    assert read <= lines_ceiling, (
        f"{name} touched {read} lines, more than the {lines_ceiling} it may:\n"
        f"{shape.describe(verbose=True)}"
    )
    if channels_ceiling is not None:
        walked = shape.rows_read("channels")
        assert walked <= channels_ceiling, (
            f"{name} walked {walked} channels, more than the {channels_ceiling} "
            f"it may:\n{shape.describe(verbose=True)}"
        )


async def _within(name: str, read, *, budget_ms: float) -> None:
    p95 = await p95_ms(read)
    print(f"\n  {name}: p95 {p95:.1f}ms (budget {budget_ms}ms)")
    assert p95 < budget_ms, f"{name}: p95 {p95:.1f}ms, over {budget_ms}ms"


# -- a channel ten thousand lines long ---------------------------------------------


async def test_a_long_channels_pages_read_only_the_page(
    pod: Pod, scale: ScalePod, db_manager
):
    base = f"{pod.base}/{scale.long_channel_id}/messages"
    newest = _get(pod, base, limit=PAGE)
    deep = _get(pod, base, limit=PAGE, before_seq=200)

    page = await newest()
    assert len(page["items"]) == PAGE and page["has_more"]
    assert page["items"][-1]["seq"] == LONG_CHANNEL_LINES
    old = await deep()
    assert len(old["items"]) == PAGE and old["items"][-1]["seq"] < 200
    first = (await _get(pod, base, after_seq=0)())["items"][0]
    assert first["seq"] == 1 and first["reply_count"] == LONG_CHANNEL_LINES // 10

    # The page and the replies it walks past -- three in twenty here -- and
    # one row per thread on it for the reply counts.
    ceiling = 2 * PAGE
    _assert_shape(
        "newest page", await shape_of(db_manager.engine, newest), lines_ceiling=ceiling
    )
    _assert_shape(
        "a deep page", await shape_of(db_manager.engine, deep), lines_ceiling=ceiling
    )
    await _within("newest page", newest, budget_ms=PAGE_BUDGET_MS)
    await _within("a deep page", deep, budget_ms=PAGE_BUDGET_MS)


async def test_a_long_thread_and_search_in_a_long_channel_stay_bounded(
    pod: Pod, scale: ScalePod, db_manager
):
    thread = _get(
        pod,
        f"{pod.base}/{scale.long_channel_id}/messages/{scale.long_thread_root_id}/thread",
        limit=PAGE,
    )
    search = f"/pods/{pod.id}/search"
    rare = _get(pod, search, q=RARE_WORD, channel_id=scale.long_channel_id)
    common = _get(pod, search, q=COMMON_WORD, channel_id=scale.long_channel_id)

    replies = await thread()
    assert len(replies["items"]) == PAGE and replies["has_more"]
    assert len((await rare())["items"]) == LONG_CHANNEL_LINES // 1000
    assert len((await common())["items"]) == SEARCH_PAGE

    _assert_shape(
        "a long thread",
        await shape_of(db_manager.engine, thread),
        lines_ceiling=PAGE + 2,
    )
    _assert_shape(
        "search, a rare word",
        await shape_of(db_manager.engine, rare),
        lines_ceiling=LONG_CHANNEL_LINES // 1000,
    )
    _assert_shape(
        "search, a common word",
        await shape_of(db_manager.engine, common),
        lines_ceiling=LONG_CHANNEL_LINES // 8,
    )
    await _within("a long thread", thread, budget_ms=THREAD_BUDGET_MS)
    await _within("search, a rare word", rare, budget_ms=RARE_SEARCH_BUDGET_MS)
    await _within("search, a common word", common, budget_ms=COMMON_SEARCH_BUDGET_MS)


# -- a member in five hundred channels ---------------------------------------------


async def test_a_members_channel_list_reads_a_page_not_every_channel(
    pod: Pod, scale: ScalePod, db_manager
):
    first = _get(pod, pod.base, limit=PAGE)
    page = await first()
    assert len(page["items"]) == PAGE
    unread = {UUID(item["id"]): item["unread"] for item in page["items"]}
    # Read to its last hundred lines, named in every tenth; fifteen of them
    # are replies, counted on their threads rather than in the timeline.
    assert unread[scale.long_channel_id] == {
        "lines": 100 - 15,
        "mentions": 10,
        "waiting": 1,
        "read_seq": LONG_CHANNEL_LINES - 100,
    }
    # Read to two lines from its end, named in the last: a channel whose
    # mentions are numbered far below the long channel's is still counted.
    assert unread[channel_id(scale.pod_id, 2)]["mentions"] == 1

    cursor = page["next_cursor"]
    for _ in range(4):
        cursor = (await _get(pod, pod.base, limit=PAGE, cursor=cursor)())["next_cursor"]
    assert cursor
    later = _get(pod, pod.base, limit=PAGE, cursor=cursor)
    later_page = await later()
    assert len(later_page["items"]) == PAGE

    # Lines are read by key only: each channel's last line. Unread mentions
    # are counted from the mention index alone -- a deleted line's went with
    # it -- so no line is read to count them. Channels are walked from the
    # cursor until a page the member may see is found: past a private group
    # they are not in for every few they are, never the pod.
    for name, read in (("the first page", first), ("a later page", later)):
        _assert_shape(
            name,
            await shape_of(db_manager.engine, read),
            lines_ceiling=PAGE,
            channels_ceiling=3 * PAGE,
        )
    await _within("channel list, first page", first, budget_ms=CHANNEL_LIST_BUDGET_MS)
    await _within("channel list, a later page", later, budget_ms=CHANNEL_LIST_BUDGET_MS)


async def test_a_members_inbox_counts_by_number_not_by_line(
    pod: Pod, scale: ScalePod, db_manager, db_session
):
    inbox = _get(pod, f"/pods/{pod.id}/inbox")
    found = await inbox()
    totals = found["totals"]
    assert totals["channels"] > MEMBER_CHANNELS // 2, totals
    assert totals["mentions"] == UNREAD_CAP, totals
    assert totals["waiting"] > 0, totals
    assert found["waiting"] and found["mentions"]

    # Lines are read by key, and only the ones the two lists show: mentions
    # are counted and listed from the member's places and the mention index
    # alone, each place one range past its read number.
    shape = await shape_of(db_manager.engine, inbox)
    _assert_shape("the inbox", shape, lines_ceiling=2 * INBOX_ITEMS)
    unread_mentions = await db_session.scalar(
        text("""
        SELECT count(*) FROM channel_mentions m
        LEFT JOIN channel_read_state r
          ON r.channel_id = m.channel_id AND r.user_id = m.user_id
        WHERE m.user_id = :member AND m.pod_id = :pod
          AND m.seq > coalesce(r.last_read_seq, 0)
        """),
        {"member": scale.member_id, "pod": scale.pod_id},
    )
    # Never every mention the member was ever given: what is unread, at most.
    assert shape.rows_read("channel_mentions") <= int(unread_mentions or 0) + (
        2 * UNREAD_CAP
    ), shape.describe(verbose=True)
    await _within("the inbox", inbox, budget_ms=INBOX_BUDGET_MS)
