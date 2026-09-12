"""Revision history: recording, resolving, and promoting."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4, uuid7

import pytest

from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRevisionEntity,
    FunctionStatus,
)
from app.modules.function.domain.errors import (
    FunctionRevisionNotFoundError,
    FunctionRevisionPrunedError,
)
from app.modules.function.services.function_revision_service import (
    FunctionRevisionService,
    parse_revision_ref,
)
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit


def _hash(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _revision(function_id, number, *, seed=None, pruned=False, schemas=None):
    # uuid7, not uuid4: the paging statement orders by id because these rows key
    # on uuid7, so id order IS creation order. A fixture minting uuid4 would let
    # the double hand back an order the database never produces.
    schemas = schemas or {}
    return FunctionRevisionEntity(
        id=uuid7(),
        function_id=function_id,
        revision_number=number,
        revision_hash=_hash(seed or str(number + 1)),
        code_path=f"revisions/{number}/function.py",
        input_schema=schemas.get("input", {"type": "object"}),
        output_schema=schemas.get("output", {"type": "object"}),
        config_schema=schemas.get("config"),
        pruned_at=None if not pruned else "2026-08-13T00:00:00Z",
    )


def _function(**overrides):
    return FunctionEntity(
        id=overrides.pop("id", uuid4()),
        pod_id=overrides.pop("pod_id", uuid4()),
        user_id=uuid4(),
        name="score_lead",
        status=FunctionStatus.READY,
        **overrides,
    )


def _service(function, revisions):
    repo = AsyncMock()
    repo.get_for_update.return_value = function
    repo.get_by_name.return_value = function
    repo.list_revisions.return_value = sorted(
        revisions, key=lambda r: r.revision_number, reverse=True
    )

    async def by_number(_function_id, number):
        return next((r for r in revisions if r.revision_number == number), None)

    repo.get_revision_by_number.side_effect = by_number
    repo.find_revisions_by_hash_prefix.side_effect = _hash_prefix_oracle(revisions)

    # The double pages the way the statement does; a fixed list here would let
    # an unpaginated caller keep passing.
    async def page(_function_id, *, limit, cursor):
        ordered = sorted(revisions, key=lambda r: r.id, reverse=True)
        if cursor is not None:
            ordered = [item for item in ordered if item.id < cursor]
        window = ordered[: limit + 1]
        next_cursor = window[limit - 1].id if len(window) > limit else None
        return window[:limit], next_cursor

    repo.page_revisions.side_effect = page
    return FunctionRevisionService(repo), repo


def _hash_prefix_oracle(revisions):
    """The Python the SQL replaced, kept here to stand in for the repository.

    These tests are about the resolver: which revision a ref names, and when it
    refuses. Running the old list-and-filter as the double keeps them that way,
    and leaves "the statement agrees with this" to
    ``test_function_revision_ref_lookup_e2e.py``, which runs both against a real
    table.
    """

    async def find(_function_id, prefix):
        if not prefix:
            return []
        matches = sorted(
            (
                item
                for item in revisions
                if item.revision_hash.removeprefix("sha256:").startswith(prefix)
            ),
            key=lambda item: (item.is_pruned, -(item.revision_number or 0)),
        )
        best: dict[str, object] = {}
        for item in matches:
            best.setdefault(item.revision_hash, item)
        return [best[digest] for digest in sorted(best)][:2]

    return find


@pytest.mark.parametrize(
    "ref,expected",
    [("12", (12, None)), ("r12", (12, None)), ("v12", (12, None))],
)
def test_parse_revision_ref(ref, expected):
    assert parse_revision_ref(ref) == expected


@pytest.mark.asyncio
async def test_resolve_by_number_and_by_hash_prefix():
    function = _function()
    revisions = [
        _revision(function.id, 1, seed="a"),
        _revision(function.id, 2, seed="b"),
    ]
    service, _ = _service(function, revisions)

    assert (await service.resolve_revision(function, "r2")).revision_number == 2
    by_hash = await service.resolve_revision(function, "aaaaaaaa")
    assert by_hash.revision_number == 1


@pytest.mark.asyncio
async def test_pruned_revision_is_refused_but_still_readable():
    function = _function()
    pruned = _revision(function.id, 1, pruned=True)
    service, _ = _service(function, [pruned])

    with pytest.raises(FunctionRevisionPrunedError):
        await service.resolve_revision(function, "r1")

    assert await service.resolve_revision(function, "r1", allow_pruned=True) is pruned


@pytest.mark.asyncio
async def test_unknown_revision_is_not_found():
    function = _function()
    service, _ = _service(function, [_revision(function.id, 1)])

    with pytest.raises(FunctionRevisionNotFoundError):
        await service.resolve_revision(function, "r9")


@pytest.mark.asyncio
async def test_list_marks_the_live_revision():
    function = _function()
    # Built in the order they were saved: ids are uuid7, so creating revision 2
    # first would give it the earlier id and the paging order would be a fact
    # about this fixture rather than about the history it stands for.
    older = _revision(function.id, 1, seed="a")
    live = _revision(function.id, 2, seed="b")
    function.revision_hash = live.revision_hash
    service, _ = _service(function, [older, live])

    listings, next_page_token = await service.list_revisions(
        function.pod_id, "score_lead", ctx=allow_all_context(), limit=50, cursor=None
    )

    assert [(item.revision.revision_number, item.is_live) for item in listings] == [
        (2, True),
        (1, False),
    ]
    assert next_page_token is None


@pytest.mark.asyncio
async def test_redeploying_a_digest_does_not_make_its_pruned_revision_live():
    function = _function(revision_hash=_hash("a"))
    pruned = _revision(function.id, 1, seed="a", pruned=True)
    live = _revision(function.id, 2, seed="a")
    service, _ = _service(function, [pruned, live])
    ctx = allow_all_context()

    listings, _ = await service.list_revisions(
        function.pod_id, function.name, ctx=ctx, limit=50, cursor=None
    )
    assert [(item.revision.revision_number, item.is_live) for item in listings] == [
        (2, True),
        (1, False),
    ]
    _, is_live = await service.get_revision(
        function.pod_id, function.name, "r1", ctx=ctx
    )
    assert not is_live
    assert await service.resolve_revision(function, "aaaaaaaa") is live


@pytest.mark.asyncio
async def test_promote_restores_the_revisions_schemas_with_its_code():
    """The schemas live on the function row and every agent and workflow bound
    to this function reads them. Promoting code without its contract would
    advertise a shape the code does not implement."""
    function = _function()
    old = _revision(
        function.id,
        1,
        seed="a",
        schemas={"input": {"type": "object", "properties": {"lead_id": {}}}},
    )
    function.revision_hash = _hash("b")
    function.input_schema = {"type": "object", "properties": {"lead": {}}}
    service, repo = _service(function, [old])
    repo.activate_revision.return_value = function

    result = await service.promote_revision(
        function.pod_id, "score_lead", "r1", ctx=allow_all_context()
    )

    _function_id, promoted = repo.activate_revision.await_args.args
    assert promoted is old
    assert result.schema_changed is True


@pytest.mark.asyncio
async def test_promote_reports_an_unchanged_contract():
    function = _function()
    old = _revision(function.id, 1, seed="a")
    function.revision_hash = _hash("b")
    function.input_schema = old.input_schema
    function.output_schema = old.output_schema
    function.config_schema = old.config_schema
    service, repo = _service(function, [old])
    repo.activate_revision.return_value = function

    result = await service.promote_revision(
        function.pod_id, "score_lead", "r1", ctx=allow_all_context()
    )

    assert result.schema_changed is False


@pytest.mark.asyncio
async def test_record_is_skipped_for_a_function_with_no_built_revision():
    """A DRAFT function created without code has nothing to index yet."""
    function = _function(revision_hash=None, code_path=None)
    service, repo = _service(function, [])

    assert await service.record(function) is None
    repo.record_revision.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_long_history_pages_rather_than_arriving_whole():
    """`PS-DATA-011`: publish a maximum and page beyond it.

    This was the only unpaginated list in the module, on a table retention
    stamps rather than empties -- so a function saved daily for a year answered
    with every one of those revisions, every time somebody opened its history.
    """
    function = _function()
    revisions = [
        _revision(function.id, number, seed=f"{number:x}c") for number in range(1, 8)
    ]
    service, _ = _service(function, revisions)
    ctx = allow_all_context()

    first, token = await service.list_revisions(
        function.pod_id, function.name, ctx=ctx, limit=3, cursor=None
    )
    assert len(first) == 3
    assert token is not None

    second, _ = await service.list_revisions(
        function.pod_id, function.name, ctx=ctx, limit=3, cursor=token
    )
    assert len(second) == 3
    assert {item.revision.id for item in first}.isdisjoint(
        {item.revision.id for item in second}
    ), "a page must not repeat what the one before it returned"
