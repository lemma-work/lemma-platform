"""Putting one envelope in front of a person, and reporting how it landed.

Split from ``base`` because it is a different kind of thing: everything there
declares what a platform *can* be asked, while this is the one behaviour shared
by all of them. Every part degrades the same way -- native first, then the
part's own ``to_plain_text``, then admit it reached nobody -- and having that
ladder in one place is what makes "never dropped for lack of native support" a
behaviour rather than a promise each platform keeps separately.
"""

from __future__ import annotations

from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    EnvelopeFile,
    EnvelopeVoice,
    PartDelivery,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS

logger = get_logger(__name__)


class EnvelopeDeliveryMixin:
    """The one outbound seam, composed over the per-content verbs on the base."""

    platform: str

    async def deliver(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryReceipt:
        """Put one envelope in front of the person, and report how each part landed.

        The one outbound seam. Today it composes the per-content verbs that
        already exist, so no platform has to change to be delivered through it;
        as each platform learns to render parts directly those verbs go away
        beneath it and this signature does not move.

        Every part degrades the same way -- native first, then the part's own
        ``to_plain_text``, then admit it reached nobody -- which is what makes
        "never dropped for lack of native support" one behaviour rather than a
        promise each platform keeps separately.

        Order is the order a person reads: narration, then what it refers to,
        then the thing being asked. An empty envelope is a caller bug and says
        so; an envelope where nothing at all landed raises, because a run
        waiting on a prompt nobody saw is the one state a person can neither
        see nor act on.
        """
        if envelope.is_empty():
            raise AgentSurfacePlatformError(
                self.platform, "refusing to deliver an empty envelope."
            )
        if self._delivers_one_reply():
            # One send, so the parts merge rather than degrade one at a time:
            # an attachment on an email is part of the reply, not a second
            # message that could fall back to a line of text.
            return await self._render_one(
                credentials=credentials,
                event=event,
                envelope=envelope,
                metadata=metadata,
            )
        return await self._deliver_each_part(
            credentials=credentials,
            event=event,
            envelope=envelope,
            metadata=metadata,
        )

    async def _deliver_each_part(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None,
    ) -> DeliveryReceipt:
        """Walk the parts, each degrading on its own. The MANY-cardinality path.

        Order is the order a person reads: narration, then what it refers to,
        then the thing being asked.
        """
        parts: dict[str, PartDelivery] = {}
        if envelope.text and envelope.text.strip():
            parts["text"] = await self._deliver_text(
                credentials=credentials,
                event=event,
                text=envelope.text,
                metadata=metadata,
            )
        for resource in envelope.resources:
            parts["resources"] = await self._deliver_resource(
                credentials=credentials,
                event=event,
                render_plan=resource,
                metadata=metadata,
            )
        for attachment in envelope.files:
            parts["files"] = await self._deliver_file(
                credentials=credentials,
                event=event,
                attachment=attachment,
                metadata=metadata,
            )
        if envelope.voice is not None:
            parts["voice"] = await self._deliver_voice(
                credentials=credentials,
                event=event,
                voice=envelope.voice,
                metadata=metadata,
            )
        if envelope.choices is not None:
            parts["choices"] = await self._deliver_choices(
                credentials=credentials,
                event=event,
                envelope=envelope,
                metadata=metadata,
            )
        if envelope.decision is not None:
            parts["decision"] = await self._deliver_decision(
                credentials=credentials,
                event=event,
                envelope=envelope,
                metadata=metadata,
            )

        receipt = DeliveryReceipt(parts=parts)
        if not receipt.delivered:
            raise AgentSurfacePlatformError(
                self.platform,
                f"nothing in this envelope reached the person ({sorted(parts)}).",
            )
        return receipt

    def _delivers_one_reply(self) -> bool:
        from app.modules.agent_surfaces.platforms.platform_capabilities import (
            DeliveryCardinality,
            get_platform_capabilities,
        )

        capabilities = get_platform_capabilities(getattr(self, "platform", None))
        return bool(
            capabilities
            and capabilities.delivery_cardinality is DeliveryCardinality.ONE
        )

    async def _render_one(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None,
    ) -> DeliveryReceipt:
        """Put a whole envelope into a single send.

        Overridden by platforms that get one reply. The default walks the parts
        anyway, so declaring ONE without implementing this degrades to sending
        several things rather than to sending nothing -- wrong, but visibly
        wrong, which is the better of the two failures.
        """
        return await self._deliver_each_part(
            credentials=credentials,
            event=event,
            envelope=envelope,
            metadata=metadata,
        )

    async def _send_text_fallback(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        text: str,
        metadata: dict[str, Any] | None,
        part: str,
    ) -> PartDelivery:
        """The last rung of every ladder: say it as a message, or admit failure."""
        try:
            await self.send_message(
                credentials=credentials, event=event, message=text, metadata=metadata
            )
        except PLATFORM_TRANSPORT_ERRORS:
            logger.warning(
                "agent_surfaces.delivery.part_reached_nobody.degraded",
                platform=self.platform,
                part=part,
            )
            return PartDelivery.UNDELIVERED
        return PartDelivery.DEGRADED

    async def _deliver_text(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        text: str,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        try:
            await self.send_message(
                credentials=credentials, event=event, message=text, metadata=metadata
            )
        except PLATFORM_TRANSPORT_ERRORS:
            logger.warning(
                "agent_surfaces.delivery.part_reached_nobody.degraded",
                platform=self.platform,
                part="text",
            )
            return PartDelivery.UNDELIVERED
        # Plain text is not a degradation of anything; it is what was asked for.
        return PartDelivery.NATIVE

    async def _deliver_choices(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        plan = envelope.choices
        assert plan is not None
        try:
            if await self._render_choices(
                credentials=credentials,
                event=event,
                question_plan=plan,
                metadata=metadata,
            ):
                return PartDelivery.NATIVE
        except PLATFORM_TRANSPORT_ERRORS:
            logger.debug(
                "agent_surfaces.delivery.native_choices_unavailable.diagnostic",
                platform=self.platform,
            )
        return await self._send_text_fallback(
            credentials=credentials,
            event=event,
            text=plan.to_plain_text(),
            metadata=metadata,
            part="choices",
        )

    async def _deliver_decision(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        plan = envelope.decision
        assert plan is not None
        try:
            if await self._render_decision(
                credentials=credentials,
                event=event,
                approval_plan=plan,
                metadata=metadata,
            ):
                return PartDelivery.NATIVE
        except PLATFORM_TRANSPORT_ERRORS:
            logger.debug(
                "agent_surfaces.delivery.native_decision_unavailable.diagnostic",
                platform=self.platform,
            )
        return await self._send_text_fallback(
            credentials=credentials,
            event=event,
            text=plan.to_plain_text(),
            metadata=metadata,
            part="decision",
        )

    async def _deliver_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        try:
            await self._render_resource(
                credentials=credentials,
                event=event,
                render_plan=render_plan,
                metadata=metadata,
            )
        except PLATFORM_TRANSPORT_ERRORS:
            return await self._send_text_fallback(
                credentials=credentials,
                event=event,
                text=render_plan.to_plain_text(),
                metadata=metadata,
                part="resources",
            )
        return PartDelivery.NATIVE

    async def _deliver_file(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: EnvelopeFile,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        try:
            if await self._render_file(
                credentials=credentials,
                event=event,
                file_name=attachment.file_name,
                file_bytes=attachment.content,
                mime_type=attachment.mime_type,
                caption=attachment.caption,
            ):
                return PartDelivery.NATIVE
        except PLATFORM_TRANSPORT_ERRORS:
            logger.debug(
                "agent_surfaces.delivery.native_attachment_unavailable.diagnostic",
                platform=self.platform,
            )
        if attachment.fallback is not None:
            # The link card is the real second rung, not a consolation line:
            # it is the same render the resource part produces, and it degrades
            # once more to text on a platform with no cards.
            outcome = await self._deliver_resource(
                credentials=credentials,
                event=event,
                render_plan=attachment.fallback,
                metadata=metadata,
            )
            return (
                PartDelivery.DEGRADED
                if outcome is not PartDelivery.UNDELIVERED
                else outcome
            )
        return await self._send_text_fallback(
            credentials=credentials,
            event=event,
            text=_file_fallback_text(attachment),
            metadata=metadata,
            part="files",
        )

    async def _deliver_voice(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        voice: EnvelopeVoice,
        metadata: dict[str, Any] | None,
    ) -> PartDelivery:
        try:
            if await self._render_voice(
                credentials=credentials,
                event=event,
                file_name=voice.file_name,
                audio_bytes=voice.content,
                mime=voice.mime_type,
                caption=voice.caption,
            ):
                return PartDelivery.NATIVE
        except PLATFORM_TRANSPORT_ERRORS:
            logger.debug(
                "agent_surfaces.delivery.native_voice_unavailable.diagnostic",
                platform=self.platform,
            )
        # A platform with no voice notes still has an audio player: the same
        # bytes as an ordinary attachment are a real delivery, not a mention
        # of one, which is why this rung is a file rather than text.
        return await self._deliver_file(
            credentials=credentials,
            event=event,
            attachment=EnvelopeFile(
                file_name=voice.file_name,
                content=voice.content,
                mime_type=voice.mime_type,
                caption=voice.caption,
                fallback=voice.fallback,
            ),
            metadata=metadata,
        )



def _file_fallback_text(attachment: EnvelopeFile) -> str:
    """What to say when the bytes themselves cannot be delivered.

    A link only opens for somebody who can sign in to the pod, so the caption
    goes with it: on the delivery where the attachment failed, the caption may
    be the only thing the person can actually read.
    """
    lines = [line for line in (attachment.caption, attachment.file_name) if line]
    return "\n".join(lines) or attachment.file_name
