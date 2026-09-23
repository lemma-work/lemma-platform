"""What one inbound message costs the database, per table.

A budget, not a plan. The plan tests next door ask whether *one* read touches the
right number of rows; this asks how many reads there are at all, because the
expensive shape here is not a bad index -- it is the same question asked four
times by four pieces of code that each resolved it for themselves.

Only this module's own tables are budgeted. An inbound message issues around 80
statements end to end, and about 50 of those belong to the agent run it starts
-- conversations, messages, runs, usage. Budgeting those here would make this
test fail for changes in a module it knows nothing about, which is how a gate
gets deleted rather than fixed.

The ceilings are the measured counts plus one. One is enough to absorb an
ordinary edit and not enough to hide a second copy of a question, which is the
regression this exists to catch. Raising one means writing down which question
is now being asked twice and why that is right.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface
from app.modules.test_support.query_counting import counted_queries

pytestmark = pytest.mark.e2e

#: The tables this module owns, and the most statements one message may issue
#: against each. Two budgets because the two messages do different work: the
#: first creates the link, the external-user row and the conversation; the
#: second finds them.
_FIRST_MESSAGE = {
    "agent_surfaces": 6,
    "agent_surface_conversation_links": 6,
    "agent_surface_external_users": 4,
    "surface_verified_identities": 2,
}
_LATER_MESSAGE = {
    "agent_surfaces": 5,
    "agent_surface_conversation_links": 7,
    "agent_surface_external_users": 4,
    "surface_verified_identities": 2,
}


def _table(statement: str) -> str:
    text = " ".join(statement.split())
    match = re.search(r"\bFROM ([a-z_]+)", text) or re.search(
        r"\b(?:INSERT INTO|UPDATE) ([a-z_]+)", text
    )
    return match.group(1) if match else "?"


def _by_table(statements: list[str]) -> Counter[str]:
    return Counter(
        _table(text)
        for text in statements
        if text.strip().upper().startswith(("SELECT", "INSERT", "UPDATE"))
    )


def _assert_within(issued: Counter[str], budget: dict[str, int], when: str) -> None:
    over = {
        table: f"{count} > {budget[table]}"
        for table, count in issued.items()
        if table in budget and count > budget[table]
    }
    assert not over, (
        f"{when}: over budget {over}\nall tables: {dict(issued.most_common())}"
    )


async def test_a_telegram_conversation_stays_inside_its_read_budget(
    db_session,
    authenticated_client,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    fake_telegram,
):
    """Two messages from a known sender on a shared bot.

    Telegram because it is the shared-bot case: no per-installation credentials
    and no workspace to narrow by, so every candidate surface in the deployment
    is in scope until the sender is resolved. If any path is going to ask a
    question twice, it is this one.
    """
    stage = await stage_surface(
        SurfacePlatform.TELEGRAM,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
        fake=fake_telegram,
    )

    with counted_queries() as first:
        await stage.say("hello")
    _assert_within(_by_table(first), _FIRST_MESSAGE, "the first message")

    with counted_queries() as later:
        await stage.say("and again")
    _assert_within(_by_table(later), _LATER_MESSAGE, "a later message")


async def test_the_budget_does_not_grow_with_the_conversation(
    db_session,
    authenticated_client,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    fake_telegram,
):
    """The fifth message costs what the second did.

    The cost that matters is the one that moves. A read that grows with the
    thread is invisible in a single measurement and fatal in a long-running
    chat, which is what a surface conversation is -- on a chat platform one
    conversation stands for the whole relationship with a person and is never
    closed.
    """
    stage = await stage_surface(
        SurfacePlatform.TELEGRAM,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
        fake=fake_telegram,
    )
    await stage.say("one")

    with counted_queries() as second:
        await stage.say("two")
    for _ in range(2):
        await stage.say("filler")
    with counted_queries() as fifth:
        await stage.say("five")

    early, late = _by_table(second), _by_table(fifth)
    grew = {
        table: f"{late[table]} vs {early[table]}"
        for table in set(_LATER_MESSAGE)
        if late[table] > early[table]
    }
    assert not grew, f"a read grew with the conversation: {grew}"
