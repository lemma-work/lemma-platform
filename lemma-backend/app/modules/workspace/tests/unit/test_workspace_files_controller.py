from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from sandbox_runtime.errors import SandboxPathNotFound, SandboxUnavailable

from sandbox_runtime.paths import HOME_ROOT, WORKSPACE_ROOT
from app.modules.workspace.api.controllers import files_controller as controller
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
)


def _stat(path: str, kind: str = "file", size: int = 12) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        kind=kind,
        size_bytes=size,
        modified_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )


# --- path clamping ----------------------------------------------------------


def test_a_relative_path_resolves_under_the_workspace_root() -> None:
    assert controller._workspace_path("notes/a.md") == f"{WORKSPACE_ROOT}/notes/a.md"
    assert controller._workspace_path(None) == f"{WORKSPACE_ROOT}"
    assert controller._workspace_path("") == f"{WORKSPACE_ROOT}"


def test_the_workspace_root_itself_is_allowed() -> None:
    assert controller._workspace_path(f"{WORKSPACE_ROOT}") == f"{WORKSPACE_ROOT}"


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/.git-credentials",
        "/tmp",
        "/etc/passwd",
        "../../etc/passwd",
        f"{HOME_ROOT}/../tmp/secret",
        f"{HOME_ROOT}/../../root",
    ],
)
def test_nothing_outside_the_workspace_is_readable(path: str) -> None:
    """`/tmp` is where the credential bridge stages secrets, so a viewer route
    that could read it would publish them to any request carrying the session."""
    with pytest.raises(HTTPException) as raised:
        controller._workspace_path(path)
    assert raised.value.status_code == 422


def test_a_traversal_that_lands_back_inside_is_allowed() -> None:
    """Refusing this would be a lie about what the path means."""
    assert (
        controller._workspace_path(f"{WORKSPACE_ROOT}/a/../b.txt")
        == f"{WORKSPACE_ROOT}/b.txt"
    )


def test_a_null_byte_is_refused() -> None:
    with pytest.raises(HTTPException) as raised:
        controller._workspace_path(f"{WORKSPACE_ROOT}/a\x00b")
    assert raised.value.status_code == 422


# --- ambient listing --------------------------------------------------------


class _FakeSession:
    def __init__(self, stats):
        self._stats = stats
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def list_files(self, path):
        return self._stats

    async def stat_file(self, path):
        """What the sandbox says about one path.

        An entry if this names one, and otherwise the thing itself as a
        directory -- which is what statting a directory returns. Returning the
        first *entry* for every path, as this used to, meant a listing of an
        empty directory had nothing to answer with.
        """
        for stat in self._stats:
            if stat.path == path:
                return stat
        return _stat(path, kind="directory", size=0)

    async def read_file(self, path, *, offset=0, length=None):
        return b"hello"


class _FakeService:
    def __init__(self, *, running: bool, stats=()):
        self.sandbox = SimpleNamespace(
            get_sandbox=self._get_sandbox,
        )
        self._running = running
        self.session = _FakeSession(list(stats))
        self.sessions_created = 0
        self.closed = False

    async def _get_sandbox(self, user_id):
        return SimpleNamespace(status="RUNNING") if self._running else None

    async def get_session(self, user_id, **kwargs):
        self.sessions_created += 1
        return self.session

    async def close(self):
        self.closed = True


def _user():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_listing_a_paused_workspace_does_not_start_it() -> None:
    """A pane that boots a sandbox on every render is a cost bug against a
    900-second idle release."""
    service = _FakeService(running=False)

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=None
    )

    assert result.sleeping is True
    assert result.entries == []
    assert service.sessions_created == 0
    assert service.closed is True


@pytest.mark.asyncio
async def test_listing_wakes_the_workspace_when_asked() -> None:
    service = _FakeService(running=False, stats=[_stat(f"{WORKSPACE_ROOT}/a.md")])

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=True, after=None
    )

    assert result.sleeping is False
    assert [entry.name for entry in result.entries] == ["a.md"]
    assert service.sessions_created == 1


@pytest.mark.asyncio
async def test_a_running_workspace_is_listed_without_being_asked_to_wake() -> None:
    service = _FakeService(
        running=True,
        stats=[_stat(f"{WORKSPACE_ROOT}/src", kind="directory", size=0)],
    )

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=None
    )

    assert result.sleeping is False
    assert result.entries[0].kind == "directory"
    assert result.entries[0].name == "src"


@pytest.mark.asyncio
async def test_a_directory_larger_than_one_page_says_so() -> None:
    stats = [
        _stat(f"{WORKSPACE_ROOT}/f{index}")
        for index in range(controller._MAX_ENTRIES + 5)
    ]
    service = _FakeService(running=True, stats=stats)

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=None
    )

    assert result.truncated is True
    assert len(result.entries) == controller._MAX_ENTRIES


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (SandboxPathNotFound("gone"), 404),
        (WorkspaceRuntimeFileNotFound("gone"), 404),
        (WorkspaceRuntimeFileRejected("big", status_code=413), 413),
        (SandboxUnavailable("paused"), 503),
    ],
)
def test_read_failures_map_to_something_the_caller_can_act_on(exc, expected) -> None:
    """The two families are parallel, not shared, so both spellings of "not
    there" have to reach the same 404."""
    assert controller._as_http_error(exc, f"{WORKSPACE_ROOT}/a").status_code == expected


def test_a_refused_credential_is_not_reported_as_a_file_that_is_too_large() -> None:
    """`SandboxUnauthorized` is a refusal, and refusals matched the size branch.

    The size branch keys on the word "Rejected", which every refusal carries.
    A runtime rejecting Lemma's own credential therefore reached the file
    explorer as 413 "File is larger than this endpoint will serve" -- sending
    the user after a problem with their file that they did not have.
    """
    from sandbox_runtime.errors import SandboxUnauthorized

    error = controller._as_http_error(
        SandboxUnauthorized("workspace runtime returned HTTP 401"),
        f"{WORKSPACE_ROOT}/a",
    )

    assert error.status_code == 503
    assert "larger" not in str(error.detail)


def test_only_real_read_failures_are_dressed_as_a_status() -> None:
    """A defect must surface as a 500, not as "the workspace is busy"."""
    assert not isinstance(TypeError("bug"), controller._READ_FAILURES)
    assert isinstance(SandboxPathNotFound("x"), controller._READ_FAILURES)


@pytest.mark.asyncio
async def test_content_is_served_as_an_attachment() -> None:
    """Workspace files are the person's own content, never markup this origin
    should render."""
    service = _FakeService(running=True, stats=[_stat(f"{WORKSPACE_ROOT}/a.html")])

    response = await controller.read_workspace_file(
        _user(), service, path="a.html", offset=0, length=None
    )

    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.media_type == "application/octet-stream"


class _MissingDirectorySession(_FakeSession):
    async def list_files(self, path):
        raise SandboxPathNotFound(path)


@pytest.mark.asyncio
async def test_a_conversation_that_has_written_nothing_is_empty_not_missing() -> None:
    """A conversation's directory does not exist until the agent writes into it,
    so every new conversation would otherwise open on a 404."""
    service = _FakeService(running=True)
    service.session = _MissingDirectorySession([])

    result = await controller.list_workspace_files(
        _user(), service, path=f"{WORKSPACE_ROOT}/conversations/abc", wake=False
    )

    assert result.entries == []
    assert result.sleeping is False


@pytest.mark.asyncio
async def test_a_missing_file_is_still_a_404() -> None:
    """Only the *listing* forgives absence; asking for one named file does not."""
    service = _FakeService(running=True)
    service.session = _MissingDirectorySession([])

    class _StatMissing(_MissingDirectorySession):
        async def stat_file(self, path):
            raise SandboxPathNotFound(path)

    service.session = _StatMissing([])
    with pytest.raises(HTTPException) as raised:
        await controller.stat_workspace_file(_user(), service, path="nope.md")
    assert raised.value.status_code == 404


# --- the boundary a symlink used to walk past --------------------------------


def test_a_symlink_is_refused_rather_than_followed() -> None:
    """The textual clamp knows nothing about links; the runtime follows them.

    So a link planted under /workspace by the agent served whatever it pointed
    at -- including `/tmp`, which the runtime allows on purpose and which is
    where a staged git credential and the relay token live.
    """
    with pytest.raises(HTTPException) as raised:
        controller._inside_workspace(
            _stat(f"{WORKSPACE_ROOT}/shortcut", kind="symlink")
        )
    assert raised.value.status_code == 422


def test_a_symlinked_parent_is_refused() -> None:
    """The case a check on the final component alone misses.

    `/workspace/x -> /tmp/lemma-relay` makes `/workspace/x/token` a perfectly
    ordinary file whose *parent* is the link. The runtime resolves the parent
    before reporting, so what comes back names `/tmp` and is refused on that.
    """
    with pytest.raises(HTTPException) as raised:
        controller._inside_workspace(_stat("/tmp/lemma-relay/token"))
    assert raised.value.status_code == 422


def test_an_ordinary_workspace_file_is_allowed() -> None:
    controller._inside_workspace(_stat(f"{WORKSPACE_ROOT}/notes/a.md"))
    controller._inside_workspace(_stat(f"{WORKSPACE_ROOT}", kind="directory", size=0))


def test_a_path_that_merely_starts_with_the_root_name_is_refused() -> None:
    """`/home/user-other` is not inside `/home/user`, and a prefix test that
    forgets the separator says it is."""
    with pytest.raises(HTTPException) as raised:
        controller._inside_workspace(_stat(f"{HOME_ROOT}-other/secrets"))
    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_a_big_directory_can_be_paged_through() -> None:
    """A truncated listing used to be a dead end.

    The response said how many were being shown and the rest could be counted
    and never reached — `node_modules` is the ordinary case, not the
    pathological one.
    """
    stats = [
        _stat(f"{WORKSPACE_ROOT}/f{index:04d}")
        for index in range(controller._MAX_ENTRIES + 5)
    ]
    service = _FakeService(running=True, stats=stats)

    first = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=None
    )

    assert first.truncated is True
    assert first.next_after == first.entries[-1].path

    rest = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=first.next_after
    )

    assert rest.truncated is False
    assert rest.next_after is None
    assert len(rest.entries) == 5
    # No overlap and nothing skipped between the pages.
    seen = [entry.path for entry in first.entries] + [e.path for e in rest.entries]
    assert len(seen) == len(set(seen)) == len(stats)


@pytest.mark.asyncio
async def test_a_listing_that_fits_offers_no_next_page() -> None:
    service = _FakeService(running=True, stats=[_stat(f"{WORKSPACE_ROOT}/a.md")])

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False, after=None
    )

    assert result.truncated is False
    assert result.next_after is None


# ---------------------------------------------------------------------------
# Asking for part of a file
# ---------------------------------------------------------------------------


def test_no_range_header_means_the_whole_file() -> None:
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        requested_range,
    )

    assert requested_range(None, 100) is None
    assert requested_range("", 100) is None


def test_a_range_becomes_an_offset_and_a_length() -> None:
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        requested_range,
    )

    assert requested_range("bytes=0-9", 100) == (0, 10)
    # An open-ended range runs to the end of the file.
    assert requested_range("bytes=10-", 100) == (10, 90)


def test_a_suffix_range_reads_the_end() -> None:
    """How a person peeks at the tail of a log without pulling all of it."""
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        requested_range,
    )

    assert requested_range("bytes=-20", 100) == (80, 20)
    # Asking for more tail than there is file is the whole file, not an error.
    assert requested_range("bytes=-500", 100) == (0, 100)


def test_a_range_past_the_end_is_refused_rather_than_clamped() -> None:
    """416 with a `Content-Range`, which is what tells a client the real size.

    Clamping would answer 206 with bytes the caller did not ask for, and a
    resuming download would stitch them into the wrong place.
    """
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        UNSATISFIABLE,
        requested_range,
    )

    assert requested_range("bytes=200-300", 100) is UNSATISFIABLE
    assert requested_range("bytes=100-", 100) is UNSATISFIABLE


def test_a_range_this_does_not_understand_is_ignored_not_refused() -> None:
    """RFC 9110's instruction, and the safe direction: the caller gets the
    whole file, which is always a correct answer to a read."""
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        requested_range,
    )

    # Multipart would mean building a multipart body, and nothing asks for one.
    assert requested_range("bytes=0-1,5-6", 100) is None
    assert requested_range("items=0-1", 100) is None
    assert requested_range("bytes=abc-def", 100) is None


def test_a_range_is_still_capped_at_one_read() -> None:
    """The ceiling is about what one request should transfer, so a range
    cannot be the way around it."""
    from app.modules.workspace.api.controllers.workspace_file_ranges import (
        MAX_CONTENT_BYTES,
        requested_range,
    )

    huge = MAX_CONTENT_BYTES * 4
    offset, length = requested_range(f"bytes=0-{huge}", huge)
    assert (offset, length) == (0, MAX_CONTENT_BYTES)


def test_content_a_caller_already_holds_is_not_sent_again() -> None:
    from app.modules.workspace.api.controllers.workspace_file_ranges import matches_etag

    assert matches_etag('"abc"', '"abc"')
    assert matches_etag("*", '"abc"'), "anything the caller has will do"
    # A weak validator is the same bytes for this purpose.
    assert matches_etag('W/"abc"', '"abc"')
    assert matches_etag('"zzz", "abc"', '"abc"'), "a list is comma-separated"
    assert not matches_etag('"zzz"', '"abc"')


class TestTheBrowserProfileIsNotServed:
    """The one thing under the durable home these routes refuse.

    Moving the browser profile out of `/tmp` and into `~/.lemma/browser` is
    what put it in range: `is_inside_home` is the only gate, and it says yes
    to anything under `/home/user`. So the cookie database and the
    local-storage LevelDB -- the live sessions of every site somebody has
    signed in to -- became downloadable over ordinary HTTP.

    That would have made a promise elsewhere in this feature into theatre:
    the listing endpoint reports hosts and expiries and deliberately never a
    cookie *value*, while the file endpoint next door served the file they
    live in. The shell inside the sandbox can still read it, which was
    accepted and written down; reachable by anything holding a URL was not.
    """

    def test_the_cookie_database_is_refused(self) -> None:
        from sandbox_runtime.paths import BROWSER_PROFILE

        with pytest.raises(HTTPException) as raised:
            controller._workspace_path(f"{BROWSER_PROFILE}/Default/Cookies")

        assert raised.value.status_code == status.HTTP_403_FORBIDDEN

    def test_the_profile_directory_itself_is_refused(self) -> None:
        from sandbox_runtime.paths import BROWSER_PROFILE_ROOT

        with pytest.raises(HTTPException) as raised:
            controller._workspace_path(BROWSER_PROFILE_ROOT)

        assert raised.value.status_code == status.HTTP_403_FORBIDDEN

    def test_traversal_back_into_the_profile_is_refused(self) -> None:
        """Normalised before it is judged, so `..` cannot walk in."""
        with pytest.raises(HTTPException) as raised:
            controller._workspace_path("/home/user/lemma/../.lemma/browser/profile")

        assert raised.value.status_code == status.HTTP_403_FORBIDDEN

    def test_an_ordinary_workspace_file_is_still_served(self) -> None:
        assert (
            controller._workspace_path("/home/user/lemma/notes.md")
            == "/home/user/lemma/notes.md"
        )

    def test_a_similarly_named_directory_is_not_caught(self) -> None:
        """`browserfoo` is not `browser/`."""
        assert controller._workspace_path("/home/user/.lemma/browserfoo/x") == (
            "/home/user/.lemma/browserfoo/x"
        )


class TestSuffixRangesObeyTheSameRules:
    """`bytes=-N` took a different path through the parser, and skipped both
    of the ordinary branch's guards."""

    def test_a_suffix_range_is_capped_like_any_other(self) -> None:
        from app.modules.workspace.api.controllers.workspace_file_ranges import (
            MAX_CONTENT_BYTES,
            requested_range,
        )

        start, length = requested_range("bytes=-999999999", total=MAX_CONTENT_BYTES * 4)

        assert length == MAX_CONTENT_BYTES, (
            "a suffix range could read far more in one response than "
            "`bytes=0-` could, which is the cap the whole-file reader is "
            "built around"
        )
        assert start == MAX_CONTENT_BYTES * 4 - 999999999 or start >= 0

    def test_the_last_bytes_of_an_empty_file_cannot_be_satisfied(self) -> None:
        """`(0, 0)` renders as `bytes 0--1/0`. There is no last byte of
        nothing, and the honest answer is 416."""
        from app.modules.workspace.api.controllers.workspace_file_ranges import (
            UNSATISFIABLE,
            requested_range,
        )

        assert requested_range("bytes=-500", total=0) is UNSATISFIABLE
