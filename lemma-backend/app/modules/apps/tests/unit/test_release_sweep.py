"""The daily release sweep: which apps a tick reaches.

The bug these exist for: the old query was ``ORDER BY id LIMIT batch_size`` with
no cursor and no filter, so every tick examined the same lowest-id apps forever.
An app that stopped being deployed -- the only case the cron exists for -- was
never reached unless it happened to sort near the front. Nothing failed; it just
quietly did a fraction of its job.
"""

from __future__ import annotations

from uuid import UUID, uuid7

import pytest

from app.modules.apps.events.tasks import _sweep

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, ids):
        self._ids = ids

    def scalars(self):
        return self

    def all(self):
        return self._ids


class _Session:
    """Answers the candidate query out of a fixed set, honouring the keyset."""

    def __init__(self, candidates: list[UUID], page_size: int):
        self.candidates = candidates
        self.page_size = page_size
        self.pages: list[list[UUID]] = []

    async def execute(self, statement):
        after = statement.compile().params.get("app_id_1")
        remaining = [c for c in self.candidates if after is None or c > after]
        page = remaining[: self.page_size]
        self.pages.append(page)
        return _Result(page)


class _Uow:
    def __init__(self, session):
        self.session = session

    async def commit(self):
        return None


class _Factory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return _Uow(self._session)

    async def __aexit__(self, *exc):
        return False


def _sorted_ids(count: int) -> list[UUID]:
    return sorted(uuid7() for _ in range(count))


@pytest.mark.asyncio
async def test_the_sweep_drains_past_the_first_page():
    """The regression. With a page size of 2 and 5 candidates, the old code
    examined 2 apps and stopped -- forever, on every tick."""
    candidates = _sorted_ids(5)
    session = _Session(candidates, page_size=2)
    seen: list[UUID] = []

    async def prune_one(_factory, app_id):
        seen.append(app_id)
        return 1

    outcome = await _sweep(
        _Factory(session), page_size=2, prune_one=prune_one
    )

    assert seen == candidates
    assert outcome.examined == 5
    assert outcome.pruned_apps == 5
    # Each page starts after the last id of the previous one -- keyset, not
    # offset, because rows leave the candidate set as they are pruned.
    assert [page[0] for page in session.pages if page] == [
        candidates[0],
        candidates[2],
        candidates[4],
    ]


@pytest.mark.asyncio
async def test_examined_is_counted_separately_from_pruned():
    """`pruned_apps += 1` per examined app could not tell a sweep that did
    nothing from one that worked."""
    candidates = _sorted_ids(3)
    session = _Session(candidates, page_size=10)

    async def prune_one(_factory, app_id):
        return 2 if app_id == candidates[0] else 0

    outcome = await _sweep(_Factory(session), page_size=10, prune_one=prune_one)

    assert outcome.examined == 3
    assert outcome.pruned_apps == 1
    assert outcome.pruned_releases == 2


@pytest.mark.asyncio
async def test_one_bad_app_does_not_stop_the_sweep():
    candidates = _sorted_ids(3)
    session = _Session(candidates, page_size=10)
    seen: list[UUID] = []

    async def prune_one(_factory, app_id):
        seen.append(app_id)
        if app_id == candidates[1]:
            raise RuntimeError("storage is unreachable")
        return 1

    outcome = await _sweep(_Factory(session), page_size=10, prune_one=prune_one)

    assert seen == candidates
    assert outcome.failed == 1
    assert outcome.pruned_apps == 2


@pytest.mark.asyncio
async def test_a_truncated_tick_says_so():
    """A budget that stops a drain mid-way has to be visible, or a permanently
    truncated sweep looks exactly like a healthy one."""
    candidates = _sorted_ids(6)
    session = _Session(candidates, page_size=2)

    async def prune_one(_factory, _app_id):
        return 1

    outcome = await _sweep(
        _Factory(session),
        page_size=2,
        budget_seconds=1e-9,  # already spent by the time the first page returns
        prune_one=prune_one,
    )

    assert outcome.truncated is True
    assert outcome.examined < len(candidates)


@pytest.mark.asyncio
async def test_a_settled_install_examines_nothing():
    """The candidate set drains. That is why this needs no cursor column: a
    pruned version leaves the set permanently, so a second tick over an install
    with nothing to do does no work at all."""
    session = _Session([], page_size=10)

    async def prune_one(_factory, _app_id):  # pragma: no cover - must not run
        raise AssertionError("nothing is prunable")

    outcome = await _sweep(_Factory(session), page_size=10, prune_one=prune_one)

    assert outcome.examined == 0
    assert outcome.pruned_apps == 0
