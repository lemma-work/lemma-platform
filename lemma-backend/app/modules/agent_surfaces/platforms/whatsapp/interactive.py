"""WhatsApp's interactive messages: choices, an approval, a resource card.

The half of `WhatsAppPlatformService` that renders a part of a delivery as an
interactive message, apart from the half that sends text, files and the typing
indicator.
"""

from __future__ import annotations

from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.envelope import PartDelivery
from app.modules.agent_surfaces.domain.reply_targets import (
    WhatsAppReplyTarget,
    reply_target_as,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.send_guard import unsendable
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.decision_cards import (
    remember_whatsapp_card,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    WhatsAppRecipient,
    build_whatsapp_approval_interactive,
    build_whatsapp_interactive,
    whatsapp_cta_url_payload,
    whatsapp_display_resource_text,
    whatsapp_recipient,
    whatsapp_text_payload,
)

logger = get_logger(__name__)


def recipient_of(event: ParsedInboundSurfaceEvent) -> WhatsAppRecipient | None:
    """Who a reply to this event goes to: its group, else the person."""
    return whatsapp_recipient(
        reply_target_as(event, WhatsAppReplyTarget), fallback_wa_id=event.sender_phone
    )


class WhatsAppInteractiveRenders:
    """Supplied by `WhatsAppPlatformService`."""

    _access_token: str
    _client: WhatsAppClient

    def _arrival_number(self, event: ParsedInboundSurfaceEvent) -> str:
        raise NotImplementedError

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        raise NotImplementedError

    async def _render_choices(
        self,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool | PartDelivery:
        """Render ask_user questions as native interactive replies.

        ≤3 options → reply buttons, 4–10 → a list; multi-select or anything that
        can't be encoded returns ``False`` so the caller falls back to text. The
        button/list ``id`` carries ``callback_id~header~value`` (no token store —
        WhatsApp ids allow 256 chars).
        """
        del metadata
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if recipient is None or not phone_number_id or not self._access_token:
            # Nothing can be sent. Declining here lets the caller's text fallback
            # try, and that hits the raising guard in `send_message`, so the
            # missing part is named there rather than in a debug line here.
            return False
        if recipient.is_group:
            # A group takes no interactive message: the question goes as text.
            return False
        sender_wa_id = recipient.to
        if any(q.multi_select for q in question_plan.questions):
            return False
        interactives = []
        for question in question_plan.questions:
            interactive = build_whatsapp_interactive(
                question_plan.callback_id, question
            )
            if interactive is None:
                return False
            interactives.append(interactive)
        for index, interactive in enumerate(interactives):
            try:
                message_id = await self._client.send_interactive(
                    phone_number_id=phone_number_id,
                    to=sender_wa_id,
                    interactive=interactive,
                )
            except platform_transport_errors():
                if not index:
                    # Nothing went out: the caller's fallback sends every
                    # question as text, and none of them is a duplicate.
                    raise
                # Earlier questions are already on the person's phone as
                # buttons. Letting this raise made the caller resend *all* of
                # them as text, so the first question appeared twice. Ask only
                # what has not been asked.
                logger.warning(
                    "agent_surfaces.service.whatsapp_questions_partially_delivered.degraded",
                    sent_questions=index,
                    total_questions=len(interactives),
                    exc_info=True,
                )
                await self.send_message(
                    event,
                    question_plan.model_copy(
                        update={"questions": question_plan.questions[index:]}
                    ).to_plain_text(),
                )
                # Delivered, but not all of it as controls: reporting True says
                # every question is tappable, and nothing then accepts a typed
                # answer to the ones that arrived as words.
                return PartDelivery.DEGRADED
            if not index:
                # The first question only: one line quoting it says the set
                # is no longer needed, where one per question would repeat it.
                await remember_whatsapp_card(
                    question_plan.callback_id,
                    message_id,
                    phone_number_id=phone_number_id,
                    recipient_wa_id=sender_wa_id,
                )
        return True

    async def _render_decision(
        self,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as WhatsApp reply buttons.

        Approve/Deny (and optionally Approve-for-session) render as ≤3 reply
        buttons; the tapped button's id carries the decision. Returns ``False``
        (caller falls back to text) when the buttons can't be encoded natively.
        """
        del metadata
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if recipient is None or not phone_number_id or not self._access_token:
            return False
        if recipient.is_group:
            # No buttons in a group: the approval is asked in words.
            return False
        sender_wa_id = recipient.to
        interactive = build_whatsapp_approval_interactive(approval_plan)
        if interactive is None:
            return False
        message_id = await self._client.send_interactive(
            phone_number_id=phone_number_id,
            to=sender_wa_id,
            interactive=interactive,
        )
        await remember_whatsapp_card(
            approval_plan.callback_id,
            message_id,
            phone_number_id=phone_number_id,
            recipient_wa_id=sender_wa_id,
        )
        return True

    async def _render_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send a resource card; True when it went as one, False when as text.

        The bool is what lets a delivery receipt tell a card from a sentence. It
        answered ``None`` -- which the adapter turned into ``True`` -- so every
        resource read as delivered natively whichever way it actually arrived.
        """
        del metadata
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if not phone_number_id or recipient is None or not self._access_token:
            raise unsendable(
                "WhatsApp",
                access_token=self._access_token,
                phone_number_id=phone_number_id,
                recipient=recipient.to if recipient else None,
            )
        action = render_plan.primary_action
        if action is None or recipient.is_group:
            # A card is an interactive message, which a group does not take;
            # there the link goes as text, with its preview.
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=whatsapp_text_payload(
                    recipient_wa_id=recipient.to,
                    recipient_type=recipient.recipient_type,
                    body=whatsapp_display_resource_text(render_plan),
                    preview_url=action is not None,
                ),
            )
            return False
        sender_wa_id = recipient.to

        try:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=whatsapp_cta_url_payload(
                    recipient_wa_id=sender_wa_id,
                    render_plan=render_plan,
                ),
            )
        except WhatsAppApiError as exc:
            # This one does recover -- the resource goes out as text below --
            # so the person is still served and this is not an error. It is
            # still a thing that failed, and at `LOG_LEVEL=INFO` a `debug` line
            # is indistinguishable from it never having happened, so the
            # fallback is recorded rather than hidden.
            logger.warning(
                "agent_surfaces.service.whatsapp_display_resource_cta_rejected.degraded",
                status_code=exc.status_code,
                exc_info=True,
            )
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=whatsapp_text_payload(
                    recipient_wa_id=sender_wa_id,
                    body=whatsapp_display_resource_text(render_plan),
                    preview_url=True,
                ),
            )
            return False
        return True
