"""Delivering a pod file to a chat surface, and what the card says when it cannot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import DisplayResourceRequest, DisplayResourceType
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.services import display_resource_content
from app.modules.agent_surfaces.services.display_resource_content import (
    PodFileDelivery,
    TablePreview,
    apply_file_facts,
    apply_table_rows,
    deliver_pod_file,
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


def _conversation_service():
    repository = SimpleNamespace(
        get_conversation=AsyncMock(return_value=SimpleNamespace(user_id=uuid4()))
    )
    return SimpleNamespace(conversation_repository=repository)


def _file_entity(*, name: str, size_bytes: int, mime_type: str):
    return SimpleNamespace(name=name, size_bytes=size_bytes, mime_type=mime_type)


@pytest.fixture
def pod_files(monkeypatch):
    """A fake pod filesystem, with the authorization plumbing stubbed out."""
    service = SimpleNamespace(
        get_file_by_path=AsyncMock(),
        download_file_content_by_path=AsyncMock(),
        render_document_page_images=AsyncMock(),
    )
    monkeypatch.setattr(
        display_resource_content, "build_file_service", lambda uow: service
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
    return service


async def test_a_file_that_fits_is_attached_and_never_becomes_a_card(pod_files):
    entity = _file_entity(name="q3.png", size_bytes=4096, mime_type="image/png")
    pod_files.get_file_by_path.return_value = entity
    pod_files.download_file_content_by_path.return_value = (entity, b"pixels")
    adapter = AsyncMock()
    adapter.send_file_attachment.return_value = True

    delivery = await deliver_pod_file(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/reports/q3.png",
        caption="q3.png",
    )

    assert delivery.delivered is True
    sent = adapter.send_file_attachment.await_args.kwargs
    assert sent["file_name"] == "q3.png"
    assert sent["file_bytes"] == b"pixels"
    assert sent["mime_type"] == "image/png"


async def test_a_pdf_arrives_with_its_first_page_shown_above_it(pod_files):
    """The picture is the point: a file name and a grey icon show nothing."""
    entity = _file_entity(
        name="shiplog.pdf", size_bytes=2_400_000, mime_type="application/pdf"
    )
    pod_files.get_file_by_path.return_value = entity
    pod_files.download_file_content_by_path.return_value = (entity, b"%PDF-1.7")
    pod_files.render_document_page_images.return_value = (
        entity,
        [SimpleNamespace(page_number=1, jpeg_bytes=b"jpeg-bytes")],
    )
    adapter = AsyncMock()
    adapter.send_file_attachment.return_value = True

    delivery = await deliver_pod_file(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/reports/shiplog.pdf",
        caption="shiplog.pdf",
        page_preview=True,
    )

    assert delivery.delivered is True
    first, second = adapter.send_file_attachment.await_args_list
    assert first.kwargs["mime_type"] == "image/jpeg"
    assert first.kwargs["file_bytes"] == b"jpeg-bytes"
    # The image carries the caption; the document under it does not repeat it.
    assert first.kwargs["caption"] == "shiplog.pdf"
    assert second.kwargs["mime_type"] == "application/pdf"
    assert second.kwargs["caption"] is None


async def test_a_page_that_will_not_render_still_sends_the_document(pod_files):
    entity = _file_entity(
        name="broken.pdf", size_bytes=1024, mime_type="application/pdf"
    )
    pod_files.get_file_by_path.return_value = entity
    pod_files.download_file_content_by_path.return_value = (entity, b"%PDF-1.7")
    pod_files.render_document_page_images.side_effect = RuntimeError("corrupt")
    adapter = AsyncMock()
    adapter.send_file_attachment.return_value = True

    delivery = await deliver_pod_file(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/broken.pdf",
        caption="broken.pdf",
        page_preview=True,
    )

    assert delivery.delivered is True
    assert adapter.send_file_attachment.await_count == 1
    # The caption survives, because nothing above it carried it.
    assert adapter.send_file_attachment.await_args.kwargs["caption"] == "broken.pdf"


async def test_an_oversize_file_comes_back_described_rather_than_sent(pod_files):
    """The card standing in for the file has to say what the file was."""
    entity = _file_entity(
        name="raw-export.zip",
        size_bytes=64 * 1024 * 1024,
        mime_type="application/zip",
    )
    pod_files.get_file_by_path.return_value = entity
    adapter = AsyncMock()

    delivery = await deliver_pod_file(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/raw-export.zip",
        caption="raw-export.zip",
    )

    assert delivery.delivered is False
    assert delivery.fits is False
    adapter.send_file_attachment.assert_not_awaited()
    # The bytes were never fetched: the size alone decided it.
    pod_files.download_file_content_by_path.assert_not_awaited()

    plan = apply_file_facts(_file_plan(), delivery)
    assert plan.title == "raw-export.zip"
    assert plan.summary == "ZIP · 64.0 MB — too large to send in this chat"


async def test_a_file_that_cannot_be_read_leaves_the_card_alone(pod_files):
    pod_files.get_file_by_path.side_effect = PermissionError("nope")
    adapter = AsyncMock()

    delivery = await deliver_pod_file(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
        target=_target(adapter),
        conversation_id=CONVERSATION_ID,
        path="/me/secret.pdf",
        caption="secret.pdf",
    )

    assert delivery == PodFileDelivery(delivered=False)
    plan = _file_plan()
    assert apply_file_facts(plan, delivery) is plan


async def test_a_table_that_cannot_be_read_still_lets_the_card_go_out():
    """Enrichment never fails the send — the whole contract of this module."""
    conversation_service = SimpleNamespace(
        conversation_repository=SimpleNamespace(
            get_conversation=AsyncMock(side_effect=AttributeError("mock uow"))
        )
    )

    preview = await resolve_table_preview(
        uow=SimpleNamespace(session=None),
        conversation_service=conversation_service,
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
    table_service = SimpleNamespace(
        get_table=AsyncMock(return_value=table),
        schema_manager=SimpleNamespace(get_schema_name=lambda pod_id: "pod_x"),
    )
    record_service = SimpleNamespace(
        list_records=AsyncMock(
            return_value=(
                [
                    SimpleNamespace(data={"id": 1, "stage": "won", "value": 4200}),
                    SimpleNamespace(data={"id": 2, "stage": "open", "value": 900}),
                ],
                42,
            )
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
        display_resource_content, "build_table_service", lambda uow: table_service
    )
    monkeypatch.setattr(
        display_resource_content, "build_record_service", lambda uow: record_service
    )

    preview = await resolve_table_preview(
        uow=SimpleNamespace(session=None),
        conversation_service=_conversation_service(),
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
    assert record_service.list_records.await_args.kwargs["filters"] == [
        ("stage", "eq", "won")
    ]
