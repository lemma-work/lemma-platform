"""A file download must not pin a pooled connection for the whole transfer.

`FileReader.download_file_content_by_path` is the short form of a split that
already existed: `resolve_readable_file_by_path` is the database half and
`read_content_for_entity` is the storage half, and both docstrings say they are
separate so a transfer does not hold a connection. The convenience wrapper
called them back to back without handing the connection back in between, so
every caller of the short form inherited the hold -- the agent's file-read tool,
the skills loader, outbound email attachments, and the memory brief that runs on
the prompt assembly of every agent run.

Fixing the wrapper rather than the six call sites is what keeps the short form
safe to use. These tests pin that, and the case where releasing would be wrong.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.services.files.reader import FileReader


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


class _Repo:
    def __init__(self, session: _Session | None) -> None:
        self.session = session


def _reader(session: _Session | None, observed: dict) -> FileReader:
    reader = FileReader.__new__(FileReader)
    reader.file_repository = _Repo(session)

    async def _resolve(pod_id, path, requester_user_id, ctx=None):
        observed["held_at_resolve"] = session.in_transaction() if session else None
        return object()

    async def _read(entity):
        observed["held_at_read"] = session.in_transaction() if session else None
        return b"bytes"

    reader.resolve_readable_file_by_path = _resolve
    reader.read_content_for_entity = _read
    return reader


@pytest.mark.asyncio
async def test_the_connection_is_back_before_the_transfer() -> None:
    session, observed = _Session(), {}

    _, content = await _reader(session, observed).download_file_content_by_path(
        "pod", "/a.md", "user"
    )

    assert content == b"bytes"
    assert observed["held_at_resolve"] is True, "the resolve is the database half"
    assert observed["held_at_read"] is False, (
        "the storage transfer ran with the pooled connection still checked out"
    )


@pytest.mark.asyncio
async def test_a_caller_mid_write_keeps_its_connection() -> None:
    """`safe_to_release` refuses, and refusing must not break the download.

    Committing underneath a caller that has written would make its writes
    durable earlier than it asked. Holding a connection is the better failure.
    """
    session, observed = _Session(), {}
    session.dirty.append(object())

    await _reader(session, observed).download_file_content_by_path(
        "pod", "/a.md", "user"
    )

    assert session.commits == 0
    assert observed["held_at_read"] is True


@pytest.mark.asyncio
async def test_no_session_is_a_no_op() -> None:
    observed: dict = {}

    _, content = await _reader(None, observed).download_file_content_by_path(
        "pod", "/a.md", "user"
    )

    assert content == b"bytes"
