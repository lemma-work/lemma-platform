"""Auto-generate a short title for a conversation.

Triggered after the first agent run completes (see
``app/modules/agent/events/handlers.py``). Generation is idempotent: it only
runs while ``conversation.title`` is unset, so a user-supplied title is never
overwritten.

By default the title is derived directly from the user's first message — no LLM
call, so titling is instant and adds no model load or DB-connection pressure.
A deployment can opt into LLM-generated titles by setting
``CONVERSATION_TITLE_MODEL`` to a model its provider actually serves; if that
call fails we still fall back to the first-message title. Failures are swallowed
and logged — titling must never break the worker that invokes it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai import Agent as PydanticAIAgent

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.realtime import (
    publish_conversation_event,
    title_updated_payload,
)
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
)
from app.modules.usage.services.pydantic_ai_tracking import (
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)
from app.modules.usage.services.usage_context import UsageExecutionContext

logger = get_logger(__name__)

_MAX_TITLE_LEN = 80

_TITLE_SYSTEM_PROMPT = (
    "You generate a concise title for a chat conversation. "
    "Respond with a short, descriptive title of 3-6 words that captures the "
    "user's intent. Return only the title text: no quotes, no surrounding "
    "punctuation, no trailing period, no prefix like 'Title:'."
)


class ConversationTitleService:
    """Generate and persist a conversation title from its opening messages."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def generate_title_if_absent(self, conversation_id: UUID) -> str | None:
        """Generate a title when one is missing; return it, or ``None`` to skip.

        Idempotent and best-effort: returns ``None`` (without raising) when the
        title already exists, there is no user message yet, or generation fails.
        """
        try:
            async with self.uow_factory() as uow:
                conversation = await ConversationRepository(uow).get_conversation(
                    conversation_id, include_messages=True
                )
            if conversation is None or conversation.title:
                return None

            messages = conversation.ordered_messages()
            user_text = _first_text(messages, MessageRole.USER.value)
            if not user_text:
                return None
            reply_text = _first_text(messages, MessageRole.ASSISTANT.value)

            # Default: derive the title from the user's first message — no LLM
            # call. Opt into LLM titles by setting CONVERSATION_TITLE_MODEL to a
            # model the provider actually serves; on any failure we still fall
            # back to the first-message title rather than leaving it blank.
            title: str | None = None
            if settings.conversation_title_model:
                try:
                    title = await self._generate(
                        user_id=conversation.user_id,
                        organization_id=conversation.organization_id,
                        pod_id=conversation.pod_id,
                        user_text=user_text,
                        reply_text=reply_text,
                    )
                except Exception as exc:
                    logger.warning(
                        "LLM title generation failed for %s, using first message: %s",
                        conversation_id,
                        exc,
                    )
            if not title:
                title = _title_from_user_message(user_text)
            if not title:
                return None

            # Re-check + persist under a fresh transaction. A concurrent run may
            # have set the title between the read above and now.
            async with self.uow_factory() as uow:
                repo = ConversationRepository(uow)
                conversation = await repo.get_conversation(conversation_id)
                if conversation is None or conversation.title:
                    return None
                conversation.title = title
                await repo.update_conversation(conversation)
                await uow.commit()

            await publish_conversation_event(
                conversation_id,
                title_updated_payload(conversation_id, title),
            )
            return title
        except Exception as exc:  # never break the calling worker
            logger.warning(
                "Conversation title generation failed for %s: %s",
                conversation_id,
                exc,
            )
            return None

    async def _generate(
        self,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        pod_id: UUID,
        user_text: str,
        reply_text: str | None,
    ) -> str | None:
        resolved = await self._resolve_runtime(
            organization_id=organization_id, user_id=user_id
        )
        runtime_profile = resolved.public_snapshot()
        model = require_pydantic_ai_model_from_runtime_profile(
            runtime_profile=runtime_profile,
            runtime_credentials=resolved.credentials or {},
            fallback_model_name=resolved.model_name_for_harness,
        )
        agent = PydanticAIAgent(model, system_prompt=_TITLE_SYSTEM_PROMPT)

        usage_context = UsageExecutionContext(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            source_type="conversation_title",
        )
        reservation = await reserve_usage_for_runtime(
            organization_id=organization_id,
            user_id=user_id,
            runtime_profile=runtime_profile,
        )
        result = None
        try:
            result = await agent.run(_build_user_prompt(user_text, reply_text))
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
                result=result,
                status="COMPLETED",
                reservation=reservation,
                metadata={"helper": "conversation_title"},
            )
        except Exception:
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
                result=result,
                status="FAILED",
                reservation=reservation,
                metadata={"helper": "conversation_title"},
            )
            raise

        return _sanitize_title(str(result.output))

    async def _resolve_runtime(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ):
        service = AgentRuntimeProfileService()
        try:
            return await service.resolve(
                runtime=AgentRuntimeConfig(
                    profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                    model_name=settings.conversation_title_model,
                ),
                organization_id=organization_id,
                user_id=user_id,
            )
        except RuntimeError:
            # Model not in this deployment's catalog — use the profile default.
            return await service.resolve(
                runtime=AgentRuntimeConfig(
                    profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                ),
                organization_id=organization_id,
                user_id=user_id,
            )


def _title_from_user_message(user_text: str) -> str:
    """Derive a title from the user's first message: collapse whitespace and
    truncate to a sentence-ish length, mirroring the surface ingress titling."""
    title = " ".join((user_text or "").split())
    if len(title) > _MAX_TITLE_LEN:
        title = f"{title[: _MAX_TITLE_LEN - 1].rstrip()}…"
    return title


def _first_text(messages, role: str) -> str | None:
    """First non-empty plain-text message body for ``role``."""
    for message in messages:
        if (
            message.role == role
            and message.kind == MessageKind.TEXT
            and message.text
            and message.text.strip()
        ):
            return message.text.strip()
    return None


def _build_user_prompt(user_text: str, reply_text: str | None) -> str:
    parts = [f"User's first message:\n{user_text}"]
    if reply_text:
        parts.append(f"Assistant's reply:\n{reply_text}")
    parts.append("Title:")
    return "\n\n".join(parts)


def _sanitize_title(raw: str) -> str:
    title = (raw or "").strip()
    if not title:
        return ""
    # Collapse to the first line — the model occasionally adds explanation.
    title = title.splitlines()[0].strip()
    # Peel trailing periods and matching wrapping quotes in any order, e.g.
    # both `"Title".` and `"Title."` reduce to `Title`.
    for _ in range(3):
        before = title
        title = title.rstrip(".").strip()
        if len(title) >= 2 and title[0] in "\"'" and title[-1] == title[0]:
            title = title[1:-1].strip()
        if title == before:
            break
    if len(title) > _MAX_TITLE_LEN:
        title = title[:_MAX_TITLE_LEN].rstrip()
    return title
