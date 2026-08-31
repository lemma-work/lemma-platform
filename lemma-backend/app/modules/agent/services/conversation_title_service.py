"""Auto-generate a short title for a conversation.

Triggered after the first agent run completes (see
``app/modules/agent/events/handlers.py``). Generation is idempotent: it only
runs while ``conversation.title`` is unset, so a user-supplied title is never
overwritten.

By default the title is derived directly from the user's first message — no LLM
call, so titling is instant and adds no model load or DB-connection pressure.
A deployment can opt into LLM-generated titles by setting
``CONVERSATION_TITLE_MODEL`` to a model its provider actually serves; if that
call fails we still fall back to the first-message title.

One attempt per conversation, and no retry: the job carries a deterministic id
so it runs once, and a fallback title is an acceptable outcome. What is *not*
acceptable is not knowing which of the two ran — a failure here is logged at
``error`` with its traceback and counted on
``lemma.agent.conversation_titles``. It stays non-fatal, because titling must
never break the worker that invokes it, but non-fatal is not the same as silent.
"""

from __future__ import annotations

from uuid import UUID

from opentelemetry import metrics
from pydantic_ai import Agent as PydanticAIAgent, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModelSettings

from app.modules.agent.config import agent_settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.realtime import (
    publish_conversation_event,
    title_updated_payload,
)
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
    usage_limits_for,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
)
from app.composition.agent_usage import (
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)
from app.composition.agent_usage import UsageExecutionContext

logger = get_logger(__name__)

meter = metrics.get_meter(__name__)
# One increment per invocation that reaches a decision, labelled with which of
# the two title paths produced it. This is the answer to "is the LLM path
# actually working?", which nothing could answer before.
title_counter = meter.create_counter("lemma.agent.conversation_titles")

_OUTCOME_LLM = "llm"
_OUTCOME_FALLBACK = "fallback"
_OUTCOME_FAILED = "failed"

_MAX_TITLE_LEN = 80
_TITLE_USAGE_LIMITS = UsageLimits(
    request_limit=1,
    input_tokens_limit=12_000,
    output_tokens_limit=256,
    total_tokens_limit=12_256,
    count_tokens_before_request=True,
)

# The title itself is 3-6 words (well under 20 tokens). Reasoning models spend
# the rest of the budget above on a hidden chain-of-thought trace nobody
# reads: verified directly against deepseek-v4-flash-0731 that the exact
# title prompt used 246 completion tokens with 239 of them reasoning, and the
# same call with reasoning_effort='none' produced the identical title in 5.
# Providers that don't recognize this key ignore it.
_TITLE_MODEL_SETTINGS: OpenAIChatModelSettings = {"openai_reasoning_effort": "none"}

_TITLE_SYSTEM_PROMPT = (
    "You generate a concise title for a chat conversation. "
    "Respond with a short, descriptive title of 3-6 words that captures the "
    "user's intent.\n\n"
    # Two rounds of this. "Write the title in the same language as the user's
    # message" fixed the case where there *is* a language to match, but said
    # nothing about the case where there is not: "hi", "test", "ok", a bare
    # URL, a code snippet. With no signal to copy, the model picked for itself
    # and a conversation opened with "hi" came back titled 初识寒暄. So the rule
    # now names the default, and names the script rather than only the
    # language -- a model asked for "English" still answered in Han characters.
    "LANGUAGE (this matters more than anything else about the title):\n"
    "- Write the title in the same language and the same script as the user's "
    "message. Never translate it into another language.\n"
    "- If the user's message is too short, too generic, or has no clear "
    "language -- a greeting, 'test', 'ok', a URL, an emoji, a code snippet, a "
    "filename -- then write the title in English. English is the default "
    "whenever you are not certain, not a language you fall back to only for "
    "English input.\n"
    "- Never answer in a language that appears nowhere in the user's message "
    "unless that language is English.\n\n"
    "Return only the title text: no quotes, no surrounding "
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
            # Two bounded rows, not the transcript: reading this with
            # ``include_messages=True`` spent 1.4s materialising every message
            # into an entity, inside an open transaction, to find two strings.
            async with self.uow_factory() as uow:
                repo = ConversationRepository(uow)
                conversation = await repo.get_conversation(conversation_id)
                if conversation is None or conversation.title:
                    return None
                opening = await repo.get_conversation_opening_texts(conversation_id)

            user_text = opening.user_text
            if not user_text:
                return None

            # Default: derive the title from the user's first message — no LLM
            # call. Opt into LLM titles by setting CONVERSATION_TITLE_MODEL to a
            # model the provider actually serves; on any failure we still fall
            # back to the first-message title rather than leaving it blank.
            title: str | None = None
            if agent_settings.conversation_title_model:
                try:
                    title = await self._generate(
                        user_id=conversation.user_id,
                        organization_id=conversation.organization_id,
                        pod_id=conversation.pod_id,
                        user_text=user_text,
                        reply_text=opening.assistant_text,
                    )
                except Exception:
                    # Not a retry point — one shot per conversation, and the
                    # fallback below is a fine answer. But it is a real failure
                    # and it gets a real record: at debug this was invisible in
                    # every deployed environment, so a provider outage and a
                    # working system looked exactly alike.
                    logger.error(
                        "agent.conversation_title.llm_call.failed",
                        conversation_id=conversation_id,
                        exc_info=True,
                    )
            if title and not title_matches_user_script(title, user_text):
                # The model answered in a script the person never used. Logged
                # rather than dropped quietly: the prompt is supposed to prevent
                # this, so every occurrence is evidence about whether it still
                # does. The fallback below is derived from their own words and
                # is right by construction.
                logger.warning(
                    "agent.conversation_title.language_mismatch.degraded",
                    conversation_id=conversation_id,
                )
                title = None
            if title:
                outcome = _OUTCOME_LLM
            else:
                outcome = _OUTCOME_FALLBACK
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
            # Counted here, at the one point where the whole thing worked, so a
            # failure anywhere above falls through to the handler below and is
            # counted once, as a failure, rather than twice.
            title_counter.add(1, {"outcome": outcome})
            return title
        except Exception:  # never break the calling worker
            logger.error(
                "agent.conversation_title.generation.failed",
                conversation_id=conversation_id,
                exc_info=True,
            )
            title_counter.add(1, {"outcome": _OUTCOME_FAILED})
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
        agent = PydanticAIAgent(model, system_prompt=_TITLE_SYSTEM_PROMPT)
        result = None
        try:
            result = await agent.run(
                _build_user_prompt(user_text, reply_text),
                usage_limits=usage_limits_for(model, _TITLE_USAGE_LIMITS),
                model_settings=_TITLE_MODEL_SETTINGS,
            )
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
                    model_name=agent_settings.conversation_title_model,
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


def _scripts_in(text: str) -> set[str]:
    """The writing systems a string actually uses, ignoring shared characters.

    Digits, punctuation and spaces say nothing about language, and Latin is
    excluded deliberately: it is the script of the default, and of the product's
    own vocabulary, so a Latin-script title is never the failure this guards
    against. What is left is the set of scripts a reader would recognise as
    "not the language I wrote in".
    """
    scripts: set[str] = set()
    for char in text:
        code = ord(char)
        if code < 0x0250:  # ASCII + Latin-1/Extended-A: Latin, digits, symbols
            continue
        if 0x0400 <= code <= 0x04FF:
            scripts.add("cyrillic")
        elif 0x0590 <= code <= 0x05FF:
            scripts.add("hebrew")
        elif 0x0600 <= code <= 0x06FF:
            scripts.add("arabic")
        elif 0x0900 <= code <= 0x097F:
            scripts.add("devanagari")
        elif 0x0E00 <= code <= 0x0E7F:
            scripts.add("thai")
        elif 0x3040 <= code <= 0x30FF:
            scripts.add("kana")
        elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
            scripts.add("hangul")
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            scripts.add("han")
    return scripts


def title_matches_user_script(title: str, user_text: str) -> bool:
    """Whether a generated title is written in a script the user actually used.

    The system prompt asks for this and mostly gets it, but "mostly" is not a
    property a title has: a conversation opened with "hi" came back titled
    初识寒暄, and the person who opened it had no way to fix it short of renaming
    the thread. Prompts are best-effort; this is the part that holds.

    A title using no script of its own (Latin, digits, punctuation) always
    passes -- that is the documented default. A title in Han characters passes
    only if the user wrote in Han characters too.
    """
    title_scripts = _scripts_in(title)
    if not title_scripts:
        return True
    return title_scripts.issubset(_scripts_in(user_text))


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
