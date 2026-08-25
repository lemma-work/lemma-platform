"""Folding a whole envelope into a single email.

A chat platform delivers each part on its own and lets each degrade on its own.
Email cannot: the person gets one message, so an attachment is *part of the
reply* rather than a second send that could fall back to a line of text, and a
question the run stopped on has to be in the body or it is not asked at all.

This is where those parts merge. It is the only thing the three email adapters
need in common beyond the transport they already share, which is why it is a
mixin rather than three copies -- the last time email had its own send path, it
read different threading headers from the shared one and silently split threads.
"""

from __future__ import annotations

from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    PartDelivery,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS

logger = get_logger(__name__)


def compose_one_reply(envelope: SurfaceEnvelope) -> str:
    """The body of the single reply, in the order a person reads it.

    A question or an approval is appended as its own text. Email has no tappable
    controls, so the part's ``to_plain_text`` -- the same degradation every
    platform without native controls gets -- is what carries it, and the typed
    reply that comes back resolves the pause exactly as a tapped button does
    elsewhere.
    """
    blocks: list[str] = []
    if envelope.text and envelope.text.strip():
        blocks.append(envelope.text.strip())
    for resource in envelope.resources:
        blocks.append(resource.to_plain_text())
    if envelope.choices is not None:
        blocks.append(envelope.choices.to_plain_text())
    if envelope.decision is not None:
        blocks.append(envelope.decision.to_plain_text())
    return "\n\n".join(blocks)


class EmailOneReplyMixin:
    """``_render_one`` for a surface whose whole run is a single reply."""

    platform: str

    async def _attachment_payload(
        self,
        credentials: dict[str, Any],
        envelope: SurfaceEnvelope,
    ) -> tuple[list[tuple[str, bytes, str]], dict[str, Any]]:
        """How this account carries files, and anything extra the body must say.

        Bytes by default. An account connected through Composio cannot take
        them -- its mail action downloads a signed URL server-side -- so that
        adapter overrides this and hands back a URL instead. Kept as a seam
        rather than a branch here because only one platform has the second
        shape, and burying it in a shared method is how it went missing the
        first time.
        """
        del credentials
        return (
            [(item.file_name, item.content, item.mime_type) for item in envelope.files],
            {},
        )

    async def _render_one(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None,
    ) -> DeliveryReceipt:
        body = compose_one_reply(envelope)
        attachments, extra = await self._attachment_payload(credentials, envelope)
        note = str(extra.pop("body_note", "") or "")
        if note:
            body = f"{body}\n\n{note}" if body else note
        if not body and not attachments and not extra:
            return DeliveryReceipt(parts={})

        # `send_message` is the shared transport, reading the same reply_target
        # every other send on this surface reads. Attachments ride the existing
        # metadata seam that display-resource plans already travel on.
        send_metadata = dict(metadata or {})
        if attachments:
            send_metadata["attachments"] = attachments
        send_metadata.update(extra)
        try:
            await self.send_message(
                credentials=credentials,
                event=event,
                message=body or " ",
                metadata=send_metadata,
            )
        except PLATFORM_TRANSPORT_ERRORS:
            logger.warning(
                "agent_surfaces.delivery.one_reply_reached_nobody.degraded",
                platform=self.platform,
            )
            return DeliveryReceipt(
                parts=dict.fromkeys(
                    _named_parts(envelope), PartDelivery.UNDELIVERED
                )
            )

        parts = {name: PartDelivery.NATIVE for name in _named_parts(envelope)}
        # Choices and a decision are text here, not controls. Recording that as
        # DEGRADED is what lets a caller tell "asked, as a written question"
        # apart from "asked, with buttons".
        for name in ("choices", "decision"):
            if name in parts:
                parts[name] = PartDelivery.DEGRADED
        return DeliveryReceipt(parts=parts)


def _named_parts(envelope: SurfaceEnvelope) -> list[str]:
    """Which parts this envelope actually carried."""
    present: list[str] = []
    if envelope.text and envelope.text.strip():
        present.append("text")
    if envelope.resources:
        present.append("resources")
    if envelope.files:
        present.append("files")
    if envelope.choices is not None:
        present.append("choices")
    if envelope.decision is not None:
        present.append("decision")
    return present
