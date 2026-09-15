"""Where a widget's HTML comes from, and who is allowed to read it."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.context import Context
from app.core.ports.widget_content import WidgetSourceUnavailable
from app.modules.agent.services.widget_asset_service import WidgetAssetService
from app.modules.datastore.contracts import (
    DatastoreAccessDeniedError,
    DatastoreFileNotFoundError,
)

POD = uuid4()
CONVERSATION = uuid4()


async def _async(value):
    return value


class _Reads:
    """The message rows one display_resource call left behind."""

    def __init__(self, rows: list[dict], pod_id=POD) -> None:
        self._rows = rows
        self._pod_id = pod_id

    async def get_tool_args_for_call(self, **_) -> list[dict]:
        return self._rows

    async def get_conversation_pod_id(self, _conversation_id):
        return self._pod_id


class _Files:
    """The pod's files, and a note of who asked for which."""

    def __init__(self, *, content: bytes | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.reads: list[tuple] = []

    async def download_file_content_by_path(self, pod_id, path, ctx):
        self.reads.append((pod_id, path, ctx))
        if self.error is not None:
            raise self.error
        body = self.content
        if callable(body):
            body = body(len(self.reads))
        return SimpleNamespace(path=path), body


def _service(rows: list[dict], files: _Files | None = None) -> WidgetAssetService:
    return WidgetAssetService(
        SimpleNamespace(session=None),
        repository=_Reads(rows),
        file_service=lambda _uow: files or _Files(),
    )


@pytest.mark.asyncio
async def test_an_inline_widget_is_read_straight_out_of_the_tool_call():
    artifact = await _service(
        [{"type": "WIDGET", "content": "<div>7 open</div>", "name": "Tickets"}]
    ).get_widget(CONVERSATION, "tc_1")
    assert artifact is not None
    assert artifact.content == "<div>7 open</div>"
    assert artifact.path is None
    assert artifact.title == "Tickets"


@pytest.mark.asyncio
async def test_a_file_backed_widget_carries_its_path_and_no_content_yet():
    # `get_widget` runs before anyone is authorized -- it exists to learn which
    # pod the widget belongs to. Reading the file here would hand it to a viewer
    # whose grants had not been consulted yet.
    artifact = await _service(
        [{"type": "WIDGET", "path": "/me/c/2026-09-15/pulse.html"}]
    ).get_widget(CONVERSATION, "tc_1")
    assert artifact is not None
    assert artifact.path == "/me/c/2026-09-15/pulse.html"
    assert artifact.content == ""


@pytest.mark.asyncio
async def test_a_call_with_neither_source_is_not_a_widget():
    assert await _service([{"type": "WIDGET"}]).get_widget(CONVERSATION, "tc_1") is None


@pytest.mark.asyncio
async def test_resolve_reads_the_file_as_the_viewer():
    files = _Files(content=b"<div>live</div>")
    service = _service([{"type": "WIDGET", "path": "/me/c/pulse.html"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    viewer = Context.__new__(Context)

    resolved = await service.resolve(artifact, viewer)

    assert resolved.content == "<div>live</div>"
    assert resolved.path == "/me/c/pulse.html"
    # Read as the person looking at it, not as the agent that wrote it.
    assert files.reads == [(POD, "/me/c/pulse.html", viewer)]


@pytest.mark.asyncio
async def test_resolve_reads_the_file_every_time_rather_than_a_stored_copy():
    """Editing the file is how a widget is corrected, so nothing may be cached."""
    files = _Files(content=lambda nth: f"<div>read {nth}</div>".encode())
    service = _service([{"type": "WIDGET", "path": "/me/c/pulse.html"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    viewer = Context.__new__(Context)

    first = await service.resolve(artifact, viewer)
    second = await service.resolve(artifact, viewer)

    assert first.content == "<div>read 1</div>"
    assert second.content == "<div>read 2</div>"


@pytest.mark.asyncio
async def test_an_inline_widget_passes_through_resolve_untouched():
    files = _Files(content=b"should not be read")
    service = _service([{"type": "WIDGET", "content": "<div>frozen</div>"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    resolved = await service.resolve(artifact, Context.__new__(Context))
    assert resolved.content == "<div>frozen</div>"
    assert files.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        DatastoreFileNotFoundError("no such file"),
        DatastoreAccessDeniedError("not yours to read"),
    ],
)
async def test_a_file_this_viewer_cannot_read_is_no_widget_for_them(refusal):
    # Deleted, moved, or not theirs to read -- one answer for all of them, and
    # none of them explains itself into a served page.
    files = _Files(error=refusal)
    service = _service([{"type": "WIDGET", "path": "/me/c/pulse.html"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    with pytest.raises(WidgetSourceUnavailable) as raised:
        await service.resolve(artifact, Context.__new__(Context))
    assert str(refusal) not in str(raised.value)
    assert "/me/c/pulse.html" in str(raised.value)


@pytest.mark.asyncio
async def test_storage_being_down_is_not_a_missing_widget():
    """A 404 would say "no widget here", which is a different and untrue thing."""
    files = _Files(error=TimeoutError("object store"))
    service = _service([{"type": "WIDGET", "path": "/me/c/pulse.html"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    with pytest.raises(TimeoutError):
        await service.resolve(artifact, Context.__new__(Context))


@pytest.mark.asyncio
async def test_a_binary_file_is_not_a_widget():
    files = _Files(content=b"\xff\xfe\x00chart.png")
    service = _service([{"type": "WIDGET", "path": "/me/c/chart.png"}], files)
    artifact = await service.get_widget(CONVERSATION, "tc_1")
    with pytest.raises(WidgetSourceUnavailable):
        await service.resolve(artifact, Context.__new__(Context))
