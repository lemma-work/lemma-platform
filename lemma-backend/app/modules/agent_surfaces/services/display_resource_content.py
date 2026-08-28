"""Reading what a displayed resource actually contains, for the surface.

The card the render plan builds knows only what the agent typed into the tool
call: a path, a table name, a filter. That is enough for a link and not enough
for a message. A person looking at their phone wants the file's size before
deciding to open it and the table's first rows instead of a promise of rows, so
this module fetches those — under the conversation owner's own authorization,
never the pod's — and hands back something the plan can carry.

It also owns getting a pod file onto the surface, because the two are the same
read: the entity that says whether the file fits is the entity whose name and
size describe it when it does not.

**Nothing here may fail the send.** Every read is enrichment; the card, and the
message behind it, must still go out when a table cannot be read or a document
will not rasterize. That promise is kept in one place — :func:`_best_effort` —
rather than by a ``try`` around each caller, so the module has one broad catch
and one answer to what happens after it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.file_types import is_untyped_mime, sniff_media_mime
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.composition.surface_datastore import (
    build_file_service,
    build_record_service,
    build_table_service,
)
from app.modules.agent.contracts import DisplayResourceRequest
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.platforms.attachment_limits import fits_inline
from app.modules.agent_surfaces.services.display_resource_preview import (
    PREVIEW_ROW_LIMIT,
    describe_file,
    format_record_count,
    format_record_table,
)
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget
from app.modules.datastore.contracts import TableContext

logger = get_logger(__name__)

T = TypeVar("T")

# The page a document is recognised by. Rendering more would be a slideshow in
# a chat; rendering none leaves a PDF as a grey glyph with a file name on it.
_PREVIEW_PAGE = 1

_PDF_MIME = "application/pdf"


@dataclass(frozen=True, slots=True)
class PodFileDelivery:
    """What became of a pod file asked for on a chat surface."""

    delivered: bool
    name: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    # Did the file clear the platform's cap? Distinguishes the one failure the
    # card can explain — a file too big for this chat — from the ones it cannot,
    # where saying nothing beats guessing at a reason.
    fits: bool = True

    @property
    def description(self) -> str | None:
        """``PDF · 2.3 MB``, for a card standing in for the file itself."""
        return describe_file(
            name=self.name, size_bytes=self.size_bytes, mime_type=self.mime_type
        )


@dataclass(frozen=True, slots=True)
class TablePreview:
    """The first rows of a table, laid out, plus how many there are in all."""

    block: str
    summary: str | None


def apply_file_facts(
    plan: SurfaceDisplayRenderPlan, delivery: PodFileDelivery
) -> SurfaceDisplayRenderPlan:
    """Put the file itself into the card that had to stand in for it.

    Only reached when the bytes did not go: the card is all the recipient gets,
    and "a file is ready to inspect" above a link they may not be able to open
    is not enough to decide anything with. Kind and size are — and the file's
    own name from the datastore beats the one parsed out of the path.
    """
    if delivery.name is None:
        return plan
    description = delivery.description
    if not delivery.fits:
        description = (
            f"{description} — too large to send in this chat"
            if description
            else "Too large to send in this chat"
        )
    return plan.model_copy(
        update={"title": delivery.name, "summary": description or plan.summary}
    )


def apply_table_rows(
    plan: SurfaceDisplayRenderPlan, preview: TablePreview | None
) -> SurfaceDisplayRenderPlan:
    """Put a table's own first rows into its card."""
    if preview is None:
        return plan
    update: dict[str, Any] = {"summary": preview.summary or plan.summary}
    if preview.block:
        update["preview_block"] = preview.block
    return plan.model_copy(update=update)


async def deliver_pod_file(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    path: str,
    caption: str | None,
    page_preview: bool = False,
) -> PodFileDelivery:
    """Attach a pod file's bytes natively when it fits the platform cap.

    ``delivered`` is True only when the file itself reached the chat. On an
    oversize file the entity's name and size still come back, so the caller's
    fallback card can say what it could not send rather than leaving the person
    to find that out by following a link.

    With ``page_preview``, a PDF is preceded by its first page as a photo. That
    is the whole reason the renderer is reached from here: a document arriving
    as a file name and a grey icon tells nobody whether it is the right one.
    """
    resolved = await _load_pod_file(
        uow=uow,
        conversation_service=conversation_service,
        target=target,
        conversation_id=conversation_id,
        path=path,
        require_inline_fit=True,
    )
    if resolved is None:
        return PodFileDelivery(delivered=False)
    entity, content, ctx = resolved
    if content is None:
        return PodFileDelivery(
            delivered=False,
            name=entity.name,
            size_bytes=entity.size_bytes,
            mime_type=entity.mime_type,
            fits=False,
        )
    if page_preview and _is_pdf(entity):
        shown = await _best_effort(
            lambda: _send_page_preview(
                uow=uow, target=target, ctx=ctx, path=path, caption=caption
            ),
            step="page_preview",
            path=path,
        )
        if shown:
            # The page image carried the caption; repeating it on the document
            # below would print the same line twice in a row.
            caption = None
    # What the file is decides how it arrives: every platform picks a photo
    # bubble, a voice note or a grey file row from this one string
    # (`media_kind_for_mime`). The datastore types a file by its name alone, so
    # anything stored without an extension claims to be a blob and reaches the
    # person as a download rather than as the picture they were sent — and the
    # bytes that would have said otherwise are already in hand here.
    mime_type = entity.mime_type
    if is_untyped_mime(mime_type):
        mime_type = sniff_media_mime(content) or "application/octet-stream"
    # No connection held for the platform call; see `connection_released`.
    async with connection_released(getattr(uow, "session", None)):
        sent = await target.adapter.send_file_attachment(
            credentials=target.credentials,
            event=target.event,
            file_name=entity.name,
            file_bytes=content,
            mime_type=mime_type,
            caption=caption,
        )
    return PodFileDelivery(
        delivered=bool(sent),
        name=entity.name,
        size_bytes=entity.size_bytes,
        mime_type=mime_type,
    )


async def load_pod_file_bytes(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    path: str,
) -> tuple[Any, bytes] | None:
    """A pod file's entity and bytes, with no size cap — for the voice path."""
    resolved = await _load_pod_file(
        uow=uow,
        conversation_service=conversation_service,
        target=target,
        conversation_id=conversation_id,
        path=path,
        require_inline_fit=False,
    )
    if resolved is None or resolved[1] is None:
        return None
    return resolved[0], resolved[1]


async def resolve_table_preview(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    request: DisplayResourceRequest,
) -> TablePreview | None:
    """The first rows of a displayed table, or ``None`` when they cannot be read."""
    return await _best_effort(
        lambda: _read_table_preview(
            uow=uow,
            conversation_service=conversation_service,
            target=target,
            conversation_id=conversation_id,
            request=request,
        ),
        step="table_preview",
        conversation_id=conversation_id,
    )


async def _read_table_preview(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    request: DisplayResourceRequest,
) -> TablePreview | None:
    pod_id = target.surface.pod_id
    ctx = await _pod_context(
        uow=uow,
        conversation_service=conversation_service,
        conversation_id=conversation_id,
        pod_id=pod_id,
    )
    if ctx is None or ctx.user_id is None:
        # Rows of an RLS table are scoped to a person. Without one there is no
        # correct set to show -- not a smaller one -- so the card keeps the
        # link and says nothing it cannot stand behind.
        return None
    user_id = ctx.user_id
    token = set_current_context(ctx)
    try:
        rows, total, columns = await _read_table_rows(
            uow=uow, pod_id=pod_id, ctx=ctx, user_id=user_id, request=request
        )
    finally:
        reset_current_context(token)
    block = format_record_table(rows, columns=columns)
    if block is None:
        return TablePreview(block="", summary=format_record_count(0, total))
    return TablePreview(block=block, summary=format_record_count(len(rows), total))


async def _read_table_rows(
    *,
    uow: Any,
    pod_id: UUID,
    ctx: Context,
    user_id: UUID,
    request: DisplayResourceRequest,
) -> tuple[list[dict[str, Any]], int | None, list[str] | None]:
    """Rows for a TABLE resource, by ad-hoc query or by table name + filters."""
    record_service = build_record_service(uow)
    table_service = build_table_service(uow)
    if request.query:
        rows, total = await record_service.execute_readonly_query(
            pod_id=pod_id,
            query=request.query,
            user_id=user_id,
            table_service=table_service,
            ctx=ctx,
        )
        return [dict(row) for row in rows[:PREVIEW_ROW_LIMIT]], total, None
    if not request.name:
        return [], None, None
    table = await table_service.get_table(pod_id, request.name, ctx)
    table_ctx = TableContext.from_table_entity(
        table,
        table_service.schema_manager.get_schema_name(pod_id),
        events_enabled=False,
    )
    records, total = await record_service.list_records(
        table_ctx,
        user_id,
        limit=PREVIEW_ROW_LIMIT,
        filters=[
            (item.field, _filter_op(item.op), item.value)
            for item in (request.filters or [])
        ]
        or None,
    )
    columns = [column.name for column in getattr(table, "columns", [])] or None
    return [dict(record.data) for record in records], total, columns


def _filter_op(op: Any) -> str:
    return str(op.value if hasattr(op, "value") else op)


async def _load_pod_file(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    path: str,
    require_inline_fit: bool,
) -> tuple[Any, bytes | None, Context] | None:
    """Resolve a pod file, and download it unless it is too big to attach.

    Returns ``(entity, content, ctx)`` where ``content`` is ``None`` for a file
    that cleared authorization but not the platform's cap — the caller still
    wants the entity, to describe what it could not send.
    """
    return await _best_effort(
        lambda: _read_pod_file(
            uow=uow,
            conversation_service=conversation_service,
            target=target,
            conversation_id=conversation_id,
            path=path,
            require_inline_fit=require_inline_fit,
        ),
        step="pod_file_read",
        conversation_id=conversation_id,
    )


async def _read_pod_file(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    path: str,
    require_inline_fit: bool,
) -> tuple[Any, bytes | None, Context] | None:
    pod_id = target.surface.pod_id
    ctx = await _pod_context(
        uow=uow,
        conversation_service=conversation_service,
        conversation_id=conversation_id,
        pod_id=pod_id,
    )
    if ctx is None:
        return None
    token = set_current_context(ctx)
    try:
        file_service = build_file_service(uow)
        entity = await file_service.get_file_by_path(pod_id, path, ctx)
        # The MIME type decides which ceiling applies on a platform that caps
        # media types separately (WhatsApp: 5 MB an image, 100 MB a document),
        # so pass it rather than let the largest one stand in.
        if require_inline_fit and not fits_inline(
            target.surface.surface_type.value,
            entity.size_bytes,
            mime_type=entity.mime_type,
        ):
            return entity, None, ctx
        _entity, content = await file_service.download_file_content_by_path(
            pod_id, path, ctx
        )
    finally:
        reset_current_context(token)
    return entity, content, ctx


async def _pod_context(
    *,
    uow: Any,
    conversation_service: Any,
    conversation_id: UUID,
    pod_id: UUID,
) -> Context | None:
    """Authorization context of the conversation's owner, in this pod.

    Everything read for a surface card is read as that person, so a resource
    they cannot see is a resource the card does not describe.
    """
    conversation = await conversation_service.conversation_repository.get_conversation(
        conversation_id
    )
    if conversation is None:
        return None
    return await create_authorization_data_service(uow).build_user_context(
        user_id=conversation.user_id,
        pod_id=pod_id,
    )


async def _send_page_preview(
    *,
    uow: Any,
    target: SurfaceEgressTarget,
    ctx: Context,
    path: str,
    caption: str | None,
) -> bool:
    """Send a document's first page as a photo, ahead of the document itself."""
    token = set_current_context(ctx)
    try:
        file_service = build_file_service(uow)
        entity, pages = await file_service.render_document_page_images(
            target.surface.pod_id,
            path,
            ctx,
            page_start=_PREVIEW_PAGE,
        )
    finally:
        reset_current_context(token)
    if not pages:
        return False
    image = pages[0].jpeg_bytes
    if not fits_inline(
        target.surface.surface_type.value, len(image), mime_type="image/jpeg"
    ):
        return False
    # No connection held for the platform call; see `connection_released`.
    async with connection_released(getattr(uow, "session", None)):
        return bool(
            await target.adapter.send_file_attachment(
                credentials=target.credentials,
                event=target.event,
                file_name=f"{entity.name}-page-{_PREVIEW_PAGE}.jpg",
                file_bytes=image,
                mime_type="image/jpeg",
                caption=caption,
            )
        )


async def _best_effort(
    action: Callable[[], Awaitable[T]],
    *,
    step: str,
    conversation_id: UUID | None = None,
    path: str | None = None,
) -> T | None:
    """Run an enrichment read, returning ``None`` if anything at all goes wrong.

    Broad on purpose, and broad in one place: the caller is mid-delivery, and
    the alternative to "no extra detail" is a message the person never receives.

    One event covers all three reads, with ``step`` naming which one gave up.
    The log catalog wants a literal event and named fields at the call site, and
    it is right to: three near-identical event names would have said no more
    than one name and a field, while costing three contracts to keep in step.
    """
    try:
        return await action()
    except Exception:
        logger.debug(
            "agent_surfaces.display_resource_content.enrichment_skipped.diagnostic",
            step=step,
            conversation_id=conversation_id,
            path=path,
            exc_info=True,
        )
        return None


def _is_pdf(entity: Any) -> bool:
    """Mirrors the document processor's own page-rendering predicate."""
    mime = str(getattr(entity, "mime_type", "") or "").split(";")[0].strip().lower()
    return mime == _PDF_MIME or str(getattr(entity, "name", "")).lower().endswith(
        ".pdf"
    )
