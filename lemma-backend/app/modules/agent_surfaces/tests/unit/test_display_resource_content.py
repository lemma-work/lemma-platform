"""Delivering a pod file to a chat surface, and what the card says when it cannot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import DisplayResourceRequest, DisplayResourceType
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.datastore.contracts.surfaces import TableRows
from app.modules.agent_surfaces.services import display_resource_content
from app.modules.agent_surfaces.services.display_resource_content import (
    PodFileDelivery,
    TablePreview,
    apply_file_facts,
    apply_table_rows,
    resolve_pod_file_parts,
    resolve_table_preview,
)

POD_ID = uuid4()
CONVERSATION_ID = uuid4()


def _target(adapter, platform: str = "TELEGRAM"):
    return SimpleNamespace(
        surface=SimpleNamespace(
            pod_id=POD_ID, surface_type=SimpleNamespace(value=platform)
        ),
        adapter=adapter,
        credentials={"bot_token": "t"},
        event=SimpleNamespace(reply_target={"chat_id": "1"}),
    )


#: Where `agent` publishes the conversation lookup a card's authorization needs.
_CONVERSATIONS = "app.modules.agent.contracts.conversations_for_surfaces"


def _file_entity(*, name: str, size_bytes: int, mime_type: str):
    return SimpleNamespace(name=name, size_bytes=size_bytes, mime_type=mime_type)


@pytest.fixture(autouse=True)
def conversation_owner(monkeypatch):
    """Whose authorization a card is rendered under.

    Doubled on `agent`'s contract rather than on this module's import of it:
    the lookup belongs to another module, and the real one reads a row.
    """
    owner = AsyncMock(return_value=SimpleNamespace(user_id=uuid4()))
    monkeypatch.setattr(f"{_CONVERSATIONS}.surface_conversation", owner)
    return owner


@pytest.fixture
def pod_files(monkeypatch):
    """Datastore's published file operations, with authorization stubbed out."""
    service = SimpleNamespace(
        read_pod_file=AsyncMock(),
        download_pod_file=AsyncMock(),
        render_pod_file_page=AsyncMock(),
    )
    for name in ("read_pod_file", "download_pod_file", "render_pod_file_page"):
        monkeypatch.setattr(display_resource_content, name, getattr(service, name))
    monkeypatch.setattr(
        display_resource_content,
        "create_authorization_data_service",
        lambda uow: SimpleNamespace(
            build_user_context=AsyncMock(
                return_value=SimpleNamespace(user_id=uuid4(), pod_id=POD_ID)
            )
        ),
    )
    return service


async def test_a_file_that_fits_is_attached_and_never_becomes_a_card(pod_files):
    entity = _file_entity(name="q3.png", size_bytes=4096, mime_type="image/png")
    pod_files.read_pod_file.return_value = entity
    pod_files.download_pod_file.return_value = (entity, b"pixels")
    resolved = await resolve_pod_file_parts(
        uow=SimpleNamespace(session=None),
        target=_target(AsyncMock()),
        conversation_id=CONVERSATION_ID,
        path="/me/reports/q3.png",
        caption="q3.png",
    )

    assert resolved.facts.delivered is True
    (attachment,) = resolved.files
    assert attachment.file_name == "q3.png"
    assert attachment.content == b"pixels"
    assert attachment.mime_type == "image/png"


async def test_a_pdf_arrives_with_its_first_page_shown_above_it(pod_files):
    """The picture is the point: a file name and a grey icon show nothing."""
    entity = _file_entity(
        name="shiplog.pdf", size_bytes=2_400_000, mime_type="application/pdf"
    )
    pod_files.read_pod_file.return_value = entity
    pod_files.download_pod_file.return_value = (entity, b"%PDF-1.7")
    pod_files.render_pod_file_page.return_value = b"jpeg-bytes"
    resolved = await resolve_pod_file_parts(
        uow=SimpleNamespace(session=None),
        target=_target(AsyncMock()),
        conversation_id=CONVERSATION_ID,
        path="/me/reports/shiplog.pdf",
        caption="shiplog.pdf",
        page_preview=True,
    )

    assert resolved.facts.delivered is True
    # One envelope, in reading order -- not two sends racing to be first.
    first, second = resolved.files
    assert first.mime_type == "image/jpeg"
    assert first.content == b"jpeg-bytes"
    # The image carries the caption; the document under it does not repeat it.
    assert first.caption == "shiplog.pdf"
    assert second.mime_type == "application/pdf"
    assert second.caption is None


async def test_a_page_that_will_not_render_still_sends_the_document(pod_files):
    entity = _file_entity(
        name="broken.pdf", size_bytes=1024, mime_type="application/pdf"
    )
    pod_files.read_pod_file.return_value = entity
    pod_files.download_pod_file.return_value = (entity, b"%PDF-1.7")
    pod_files.render_pod_file_page.side_effect = RuntimeError("corrupt")
    resolved = await resolve_pod_file_parts(
        uow=SimpleNamespace(session=None),
        target=_target(AsyncMock()),
        conversation_id=CONVERSATION_ID,
        path="/me/broken.pdf",
        caption="broken.pdf",
        page_preview=True,
    )

    assert resolved.facts.delivered is True
    (document,) = resolved.files
    # The caption survives, because nothing above it carried it.
    assert document.caption == "broken.pdf"


async def test_an_oversize_file_comes_back_described_rather_than_sent(pod_files):
    """The card standing in for the file has to say what the file was."""
    entity = _file_entity(
        name="raw-export.zip",
        size_bytes=64 * 1024 * 1024,
        mime_type="application/zip",
    )
    pod_files.read_pod_file.return_value = entity
    adapter = AsyncMock()

    resolved = await resolve_pod_file_parts(
        uow=SimpleNamespace(session=None),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/raw-export.zip",
        caption="raw-export.zip",
    )

    assert resolved.files == [], "an oversize file must not become an attachment"
    assert resolved.facts.fits is False
    # The bytes were never fetched: the size alone decided it.
    pod_files.download_pod_file.assert_not_awaited()

    plan = apply_file_facts(_file_plan(), resolved.facts)
    assert plan.title == "raw-export.zip"
    assert plan.summary == "ZIP · 64.0 MB — too large to send in this chat"


async def test_a_file_that_cannot_be_read_leaves_the_card_alone(pod_files):
    pod_files.read_pod_file.side_effect = PermissionError("nope")
    adapter = AsyncMock()

    resolved = await resolve_pod_file_parts(
        uow=SimpleNamespace(session=None),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/secret.pdf",
        caption="secret.pdf",
    )

    assert resolved.files == []
    assert resolved.facts == PodFileDelivery(delivered=False)
    plan = _file_plan()
    assert apply_file_facts(plan, resolved.facts) is plan


async def test_a_table_that_cannot_be_read_still_lets_the_card_go_out(
    conversation_owner,
):
    """Enrichment never fails the send — the whole contract of this module."""
    conversation_owner.side_effect = AttributeError("mock uow")

    preview = await resolve_table_preview(
        uow=SimpleNamespace(session=None),
        target=_target(AsyncMock()),
        conversation_id=CONVERSATION_ID,
        request=DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    assert preview is None
    plan = _table_plan()
    assert apply_table_rows(plan, preview) is plan


def test_table_rows_land_in_the_card_with_the_count():
    plan = apply_table_rows(
        _table_plan(),
        TablePreview(block="id  stage\n--  -----\n1   won", summary="1 of 42 records"),
    )

    assert plan.summary == "1 of 42 records"
    assert plan.preview_block is not None
    assert "1   won" in plan.preview_block


def _file_plan() -> SurfaceDisplayRenderPlan:
    return SurfaceDisplayRenderPlan(resource_type="FILE", title="raw-export.zip")


def _table_plan() -> SurfaceDisplayRenderPlan:
    return SurfaceDisplayRenderPlan(resource_type="TABLE", title="Table: deals")


async def test_a_displayed_table_arrives_with_its_own_first_rows(monkeypatch):
    """The point of the whole path: the rows, not a sentence promising rows."""
    columns = [
        SimpleNamespace(name="id"),
        SimpleNamespace(name="stage"),
        SimpleNamespace(name="value"),
    ]
    table = SimpleNamespace(
        pod_id=POD_ID,
        id=uuid4(),
        table_name="deals",
        columns=columns,
        primary_key_column="id",
        enable_rls=False,
    )
    read_table_preview = AsyncMock(
        return_value=TableRows(
            rows=[
                {"id": 1, "stage": "won", "value": 4200},
                {"id": 2, "stage": "open", "value": 900},
            ],
            total=42,
            columns=[column.name for column in table.columns],
        )
    )
    monkeypatch.setattr(
        display_resource_content,
        "create_authorization_data_service",
        lambda uow: SimpleNamespace(
            build_user_context=AsyncMock(
                return_value=SimpleNamespace(user_id=uuid4(), pod_id=POD_ID)
            )
        ),
    )
    monkeypatch.setattr(
        display_resource_content, "read_table_preview", read_table_preview
    )

    preview = await resolve_table_preview(
        uow=SimpleNamespace(session=None),
        target=_target(AsyncMock()),
        conversation_id=CONVERSATION_ID,
        request=DisplayResourceRequest(
            type=DisplayResourceType.TABLE,
            name="deals",
            filters=[{"field": "stage", "op": "eq", "value": "won"}],
        ),
    )

    assert preview is not None
    assert preview.summary == "2 of 42 records"
    assert "won" in preview.block
    # The schema's column order is what the block uses, not the dict's.
    assert preview.block.splitlines()[0].split() == ["id", "stage", "value"]
    # The displayed filters are the ones the rows were read under.
    assert read_table_preview.await_args.kwargs["filters"] == [("stage", "eq", "won")]
    assert read_table_preview.await_args.kwargs["table_name"] == "deals"
