"""Release history: resolving a reference, listing, and promoting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity, AppStatus
from app.modules.apps.domain.errors import (
    AppReleaseNotFoundError,
    AppReleasePrunedError,
)
from app.modules.apps.services.app_release_service import (
    AppReleaseService,
    parse_release_ref,
)
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _release(app_id, number, *, version=None, pruned=False, source="source/aa/a.zip"):
    return AppReleaseEntity(
        id=uuid4(),
        app_id=app_id,
        # A realistic digest: hex, and not a run of leading zeros that would make
        # a short prefix look like a release number.
        version=version or (f"{number:x}e7d9c" * 12)[:64],
        release_number=number,
        dist_root_path=f"releases/{number}/dist/",
        source_archive_path=source,
        pruned_at=_NOW if pruned else None,
        created_at=_NOW - timedelta(days=number),
    )


def _service(app, releases):
    repo = AsyncMock()
    repo.get_by_name.return_value = app
    # Newest first, matching AppRepository.list_releases' ordering.
    repo.list_releases.return_value = sorted(
        releases, key=lambda r: r.release_number, reverse=True
    )

    async def by_number(_app_id, number):
        return next(
            (r for r in releases if r.release_number == number), None
        )

    repo.get_release_by_number.side_effect = by_number
    return AppReleaseService(repo), repo


def _app(**overrides):
    return AppEntity(
        id=overrides.pop("id", uuid4()),
        pod_id=overrides.pop("pod_id", uuid4()),
        user_id=uuid4(),
        name="orders",
        public_slug="orders",
        **overrides,
    )


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("7", (7, None)),
        ("v7", (7, None)),
        ("r7", (7, None)),  # the form the preview host uses
        ("V7", (7, None)),
        ("9f8e7d", (None, "9f8e7d")),
        ("sha256:9f8e7d", (None, "9f8e7d")),
    ],
)
def test_parse_release_ref(ref, expected):
    assert parse_release_ref(ref) == expected


@pytest.mark.asyncio
async def test_resolve_by_number_and_by_digest_prefix():
    app = _app()
    releases = [_release(app.id, 1), _release(app.id, 2)]
    service, _ = _service(app, releases)

    by_number = await service.resolve_release(app, "v2")
    assert by_number.release_number == 2

    by_digest = await service.resolve_release(app, releases[0].version[:8])
    assert by_digest.release_number == 1


@pytest.mark.asyncio
async def test_ambiguous_digest_prefix_is_refused():
    """Promoting the wrong build is not a recoverable mistake, so an ambiguous
    prefix is an error rather than "the newest match"."""
    app = _app()
    releases = [
        _release(app.id, 1, version="abc" + "0" * 61),
        _release(app.id, 2, version="abc" + "1" * 61),
    ]
    service, _ = _service(app, releases)

    with pytest.raises(AppReleaseNotFoundError, match="ambiguous"):
        await service.resolve_release(app, "abc")


@pytest.mark.asyncio
async def test_all_digit_digest_prefix_falls_back_to_a_digest_lookup():
    """A digest is hex, so a short prefix can be all decimal digits and read as
    a release number. It must still resolve rather than 404."""
    app = _app()
    numeric_digest = _release(app.id, 1, version="12345678" + "ab" * 28)
    service, _ = _service(app, [numeric_digest])

    resolved = await service.resolve_release(app, "12345678")

    assert resolved is numeric_digest


@pytest.mark.asyncio
async def test_unknown_release_is_not_found():
    app = _app()
    service, _ = _service(app, [_release(app.id, 1)])

    with pytest.raises(AppReleaseNotFoundError):
        await service.resolve_release(app, "v9")


@pytest.mark.asyncio
async def test_pruned_release_is_refused_but_still_listable():
    """A pruned release keeps its row so history stays legible, but its bytes
    are gone -- serving or promoting it has to fail with that reason, not 404."""
    app = _app()
    pruned = _release(app.id, 1, pruned=True)
    service, _ = _service(app, [pruned, _release(app.id, 2)])

    with pytest.raises(AppReleasePrunedError):
        await service.resolve_release(app, "v1")

    assert (await service.resolve_release(app, "v1", allow_pruned=True)) is pruned


@pytest.mark.asyncio
async def test_list_marks_the_live_release():
    app = _app()
    live = _release(app.id, 2)
    app.current_release_id = live.id
    service, _ = _service(app, [_release(app.id, 1), live])

    history = await service.list_releases(app.pod_id, "orders", ctx=allow_all_context())

    assert history.app_public_slug == "orders"
    assert [(e.release.release_number, e.is_live) for e in history.items] == [
        (2, True),
        (1, False),
    ]


@pytest.mark.asyncio
async def test_promote_moves_the_pointer_and_the_source_with_it():
    """An export taken after a rollback must ship the source that produced the
    running build, so the app's source pointer follows the promoted release."""
    app = _app(status=AppStatus.READY)
    old = _release(app.id, 1, source="source/old/archive.zip")
    new = _release(app.id, 2, source="source/new/archive.zip")
    app.current_release_id = new.id
    app.source_archive_path = "source/new/archive.zip"
    service, repo = _service(app, [old, new])

    promoted = await service.promote_release(
        app.pod_id, "orders", "v1", ctx=allow_all_context()
    )

    assert promoted.release_number == 1
    updated = repo.update.await_args.args[0]
    assert updated.current_release_id == old.id
    assert updated.source_archive_path == "source/old/archive.zip"


@pytest.mark.asyncio
async def test_promote_keeps_working_source_when_the_release_predates_the_column():
    """Releases backfilled before per-release source existed carry none.
    Overwriting the app's working pointer with NULL would lose the source."""
    app = _app()
    old = _release(app.id, 1, source=None)
    app.source_archive_path = "source/new/archive.zip"
    app.current_release_id = uuid4()
    service, repo = _service(app, [old])

    await service.promote_release(app.pod_id, "orders", "v1", ctx=allow_all_context())

    assert repo.update.await_args.args[0].source_archive_path == "source/new/archive.zip"


@pytest.mark.asyncio
async def test_promoting_the_live_release_is_a_no_op():
    app = _app()
    live = _release(app.id, 1)
    app.current_release_id = live.id
    service, repo = _service(app, [live])

    await service.promote_release(app.pod_id, "orders", "v1", ctx=allow_all_context())

    repo.update.assert_not_awaited()
