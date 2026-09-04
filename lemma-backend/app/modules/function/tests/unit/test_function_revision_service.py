"""Revision history: recording, resolving, and promoting."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

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
    schemas = schemas or {}
    return FunctionRevisionEntity(
        id=uuid4(),
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
    repo.get_by_name.return_value = function
    repo.list_revisions.return_value = sorted(
        revisions, key=lambda r: r.revision_number, reverse=True
    )

    async def by_number(_function_id, number):
        return next((r for r in revisions if r.revision_number == number), None)

    repo.get_revision_by_number.side_effect = by_number
    return FunctionRevisionService(repo), repo


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
    live = _revision(function.id, 2, seed="b")
    function.revision_hash = live.revision_hash
    service, _ = _service(function, [_revision(function.id, 1, seed="a"), live])

    listings = await service.list_revisions(
        function.pod_id, "score_lead", ctx=allow_all_context()
    )

    assert [(item.revision.revision_number, item.is_live) for item in listings] == [
        (2, True),
        (1, False),
    ]


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
