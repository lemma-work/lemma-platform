"""WhatsApp surface adapter."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
    split_whatsapp_deliveries,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)


class WhatsAppSurfaceAdapter(BaseSurfaceAdapter):
    platform = "WHATSAPP"

    def __init__(self) -> None:
        self._parser = WhatsAppMessageParser()

    def split_inbound_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return split_whatsapp_deliveries(payload)

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self._parser.parse(payload, headers)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return await WhatsAppPlatformService(credentials).fetch_sender_profile(event)

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await WhatsAppPlatformService(credentials).send_message(
            event, message, metadata
        )

    async def _render_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        await WhatsAppPlatformService(credentials)._render_resource(
            event,
            render_plan,
            metadata,
        )
        return True

    async def _render_choices(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials)._render_choices(
            event, question_plan, metadata
        )

    async def _render_decision(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials)._render_decision(
            event, approval_plan, metadata
        )

    async def acknowledge_interaction(
        self,
        *,
        credentials: dict[str, Any],
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        await WhatsAppPlatformService(credentials).acknowledge_interaction(
            interaction,
            text=text,
            show_alert=show_alert,
            clear_actions=clear_actions,
        )

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self._parser.parse_interaction(payload, headers)

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Post a progress update as a new message.

        Returns ``None`` deliberately: a handle is a reference to a live message
        that later edits and the end-of-run cleanup act on, and WhatsApp has no
        such message. Each update stands on its own and there is nothing to
        clear afterwards.
        """
        del progress_handle, metadata
        await WhatsAppPlatformService(credentials).stream_progress(event, progress_text)
        return None

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await WhatsAppPlatformService(credentials).add_processing_indicator(
            event, metadata
        )

    def unresolved_sender_reply(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None:
        """Say which number we did not recognise, rather than "please sign up".

        The default prompt tells the sender to create an account, which is
        wrong for most people who reach this: they have one, and what is
        missing is the mobile number on it. Meta signed the payload carrying
        their ``wa_id``, so the number is not a guess -- naming it turns an
        inaccurate instruction into the one fact that lets someone fix this,
        and it is their own number, which they already know.

        Nothing is claimed about whether an account exists. That question is
        only answerable to someone who has proved who they are, and saying
        either answer here would tell any sender whether a number is
        registered.
        """
        if not event.is_dm:
            return None
        # Meta's `wa_id` is E.164 without the `+`, and the parser stores it
        # verbatim. Everywhere a person sees their own number it has the `+`.
        digits = "".join(c for c in str(event.sender_phone or "") if c.isdigit())
        if not digits:
            return None
        number = f"+{digits}"
        profile_url = f"{settings.frontend_url.rstrip('/')}/profile"
        return (
            f"I don't recognise {number}. Add it as the mobile number on your "
            f"Lemma profile and I'll know it's you: {profile_url}",
            {},
        )

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        return await WhatsAppPlatformService(credentials).download_attachment_bytes(
            event, attachment
        )

    async def _render_file(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials).send_file_bytes(
            event,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            caption=caption,
        )
