"""File search must not hold a platform connection across the embedding call.

A vector or hybrid search embeds the query with the provider before it can query
anything. `pod_search_files` runs inside `pod_services`, which holds a platform
database connection in an open transaction for the whole tool call -- so the
connection sat idle for the entire provider round trip.

Measured in production over a week: 105 holds from this one tool, median 4.3s,
maximum 33s, `in_transaction` on every one of them and ~97% of the time idle.
The worst hold in the whole dataset was the sibling `pod_view_document_pages` at
47s, of which 47s was idle and 24ms was querying.

The release has to wrap the search call and nothing wider. `connection_released`
releases once, on entry, so a block that reads the platform database first would
re-acquire the connection and hold it across the slow part anyway -- while the
static gate went quiet, which is worse than not fixing it.
"""

from __future__ import annotations

from uuid import uuid7

import pytest

from app.modules.datastore.domain.file_entities import (
    DatastoreFileSearchResult,
    SearchMethod,
)
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.services.files.searcher import FileSearcher


class _Paths:
    """Only the `/me` translation the searcher applies to a returned row."""

    def _to_api_path(self, path: str, *, requester_user_id: object) -> str:
        return path


def _make_result(file_id):
    return DatastoreFileSearchResult(
        file_id=file_id,
        path="/notes.md",
        chunk_index=0,
        content="anything",
        metadata={},
        score=1.0,
    )


class _Session:
    """Enough of an AsyncSession for `safe_to_release` to say yes."""

    def __init__(self) -> None:
        self.new: list[object] = []
        self.dirty: list[object] = []
        self.deleted: list[object] = []
        self.info: dict[str, object] = {}
        self.commits = 0

    def in_transaction(self) -> bool:
        return self.commits == 0

    async def commit(self) -> None:
        self.commits += 1


class _Authorizer:
    """Carries the repository the searcher takes its platform session from.

    Production wires it the same way: `FileAuthorizer` is built with the
    `DatastoreFileRepository`, whose `session` is the platform session.

    `enumerated` picks which of the two branches the searcher takes, because
    they hold the connection differently: an enumerated scope does all its
    platform reads before the search, and a post-filtered one has to do one
    afterwards.
    """

    def __init__(
        self,
        session: "_Session | None" = None,
        *,
        enumerated: bool = True,
    ) -> None:
        self._session = session
        self._enumerated = enumerated
        self.authorized: set | None = None
        self.file_repository = (
            None if session is None else type("_Repo", (), {"session": session})()
        )

    async def search_file_scope(
        self, *, pod_id: object, ctx: object
    ) -> SearchFileScope:
        return (
            SearchFileScope.only([uuid7()])
            if self._enumerated
            else SearchFileScope.post_filtered()
        )

    async def readable_among(
        self, *, pod_id: object, ctx: object, file_ids: object
    ) -> set:
        self.authorized = set(file_ids)
        return self.authorized


class _SearchService:
    """Stands in for the embedding + vector query, recording what it saw."""

    def __init__(
        self, session: _Session, results: "list[object] | None" = None
    ) -> None:
        self._session = session
        self._results = results or []
        self.held_a_connection: bool | None = None

    async def search(self, **_kwargs: object) -> list[object]:
        self.held_a_connection = self._session.in_transaction()
        return list(self._results)


def _searcher(
    session: "_Session | None",
    service: _SearchService,
    *,
    authorizer: _Authorizer | None = None,
) -> FileSearcher:
    return FileSearcher(
        lambda: lambda _pod_id: service,
        authz=None,
        authorizer=authorizer or _Authorizer(session),
        path_resolver=_Paths(),
        lookup=None,
    )


@pytest.mark.parametrize(
    "method",
    [SearchMethod.VECTOR, SearchMethod.HYBRID, SearchMethod.TEXT],
    ids=["vector", "hybrid", "text"],
)
async def test_the_platform_connection_is_released_for_the_search(method) -> None:
    """The search runs with the platform connection handed back."""
    session = _Session()
    service = _SearchService(session)

    await _searcher(session, service).search_files(
        pod_id=uuid7(),
        requester_user_id=uuid7(),
        query="anything",
        search_method=method,
        ctx=object(),
    )

    assert session.commits == 1, "the platform connection was never handed back"
    assert service.held_a_connection is False, (
        "the search ran while the platform transaction was still open"
    )


async def test_a_caller_with_pending_writes_keeps_its_connection() -> None:
    """`safe_to_release` refuses, and refusing must not break the search.

    Committing underneath a caller that has written would make its writes
    durable earlier than it asked. Holding a connection is the better failure.
    """
    session = _Session()
    session.dirty.append(object())
    service = _SearchService(session)

    await _searcher(session, service).search_files(
        pod_id=uuid7(),
        requester_user_id=uuid7(),
        query="anything",
        ctx=object(),
    )

    assert session.commits == 0
    assert service.held_a_connection is True


async def test_no_session_is_a_no_op() -> None:
    """A service built without one (a test double, a caller that has none)."""
    session = _Session()
    service = _SearchService(session)

    searcher = _searcher(None, service)
    await searcher.search_files(
        pod_id=uuid7(),
        requester_user_id=uuid7(),
        query="anything",
        ctx=object(),
    )

    assert session.commits == 0


async def test_an_unnarrowed_search_authorizes_the_rows_it_got_back() -> None:
    """The post-filter branch still releases, and still authorizes.

    An unnarrowed chunk query returns rows without asking whether the caller
    may read them, so the authorization is not an extra check here -- it is
    the only one. It reads the platform database, which is why it is placed
    after the released block and not inside it: `connection_released` releases
    once, on entry, so a read inside re-acquires the connection and holds it
    for the rest of the block while the static gate goes quiet.
    """
    session = _Session()
    seen = _make_result(uuid7())
    service = _SearchService(session, [seen])
    authorizer = _Authorizer(session, enumerated=False)

    await _searcher(session, service, authorizer=authorizer).search_files(
        pod_id=uuid7(),
        requester_user_id=uuid7(),
        query="anything",
        ctx=object(),
    )

    assert service.held_a_connection is False, (
        "the search ran while the platform transaction was still open"
    )
    assert authorizer.authorized == {seen.file_id}, (
        "an unnarrowed search returned rows nobody asked the platform database about"
    )
