"""Asking a small model who should answer, and never letting it do more.

The decision rules live in ``agent_router``; they are pure and they settle most
messages without anything here running. This is only the leftover case: nobody
was addressed, several agents are present, and a person wrote it.

Three properties matter more than accuracy:

- **It cannot answer.** The model is asked for a name and its output is matched
  against the roster. Anything else is silence, so a wrong call costs one
  skipped reply rather than a fabricated one.
- **It cannot fail loudly.** A router that raises would break a message send
  over a question whose correct default answer is "nobody". Every failure is
  logged and returns silence.
- **It is small and short.** One message, the roster, no history. Routing runs
  on messages that are usually not for any agent, so it has to cost almost
  nothing to be wrong about.
"""

from __future__ import annotations

from uuid import UUID

from opentelemetry import metrics
from pydantic import BaseModel, Field
from pydantic_ai import Agent as PydanticAIAgent, ModelSettings, UsageLimits

from app.composition.agent_usage import (
    UsageExecutionContext,
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.agent_router import (
    InboundMessage,
    RosterAgent,
    addressed_agent,
    resolve_router_choices,
    router_prompt,
    routing_is_needed,
)
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
    usage_limits_for,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
)

logger = get_logger(__name__)

meter = metrics.get_meter(__name__)
#: One increment per routed message, labelled with what happened to it:
#: `addressed` (never reached a model), `skipped` (a structural guard), `agent`
#: (a model chose one) or `silent` (a model chose nobody, or failed).
router_counter = meter.create_counter("lemma.agent.message_routes")

_ROUTER_SYSTEM_PROMPT = (
    "You decide which agent, if any, a message is for. You never answer the "
    "message. You reply with one agent name, or the single word NONE."
)
#: Enough for a reasoning model to think and then answer. It was 32 -- the
#: length of a name -- which looked frugal and was the whole bug: a reasoning
#: model narrates before it answers, so the cap truncated every reply mid-
#: sentence and the name never arrived. Every message routed to nobody, and the
#: tolerant parser below correctly read the prose as "no choice".
_ROUTER_USAGE_LIMITS = UsageLimits(request_limit=2, output_tokens_limit=1024)
#: `thinking=False` because routing is a lookup, not a problem: the roster is
#: in the prompt and the answer is one of its names. Reasoning here bought
#: nothing and cost a multiple of the latency on every unaddressed message.
#: Silently ignored by models that always reason, which is why the structured
#: output below is what actually guarantees a usable answer.
_ROUTER_MODEL_SETTINGS = ModelSettings(temperature=0.0, thinking=False)


class RouterChoice(BaseModel):
    """The router's answer, as a field rather than as prose.

    Structured output is what makes the answer separable from the thinking. A
    model that reasons out loud still has to put its choice here, so the reply
    cannot be lost in the narration -- which is exactly how free text failed:
    the cap truncated every reply mid-sentence and the name never arrived.
    """

    agents: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the agents who should answer, exactly as listed, most "
            "relevant first. Empty when the message is not for any of them."
        ),
    )


async def resolve_responder(
    message: InboundMessage,
    roster: list[RosterAgent],
    *,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID,
    recent: list[str] | None = None,
) -> list[UUID]:
    """Which agents answer this message, most relevant first. Empty for nobody.

    Stage one first and without a model. Only what is left reaches one.
    """
    addressed = addressed_agent(message, roster)
    if addressed is not None:
        router_counter.add(1, {"outcome": "addressed"})
        return [addressed]
    if not routing_is_needed(message, roster):
        router_counter.add(1, {"outcome": "skipped"})
        return []

    chosen = await _ask_model(
        message,
        roster,
        user_id=user_id,
        organization_id=organization_id,
        pod_id=pod_id,
        recent=recent,
    )
    router_counter.add(1, {"outcome": "agent" if chosen else "silent"})
    return chosen


async def _ask_model(
    message: InboundMessage,
    roster: list[RosterAgent],
    *,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID,
    recent: list[str] | None = None,
) -> list[UUID]:
    try:
        resolved = await _resolve_runtime(
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
            source_type="agent_router",
        )
        reservation = await reserve_usage_for_runtime(
            organization_id=organization_id,
            user_id=user_id,
            runtime_profile=runtime_profile,
        )
        agent = PydanticAIAgent(
            model,
            system_prompt=_ROUTER_SYSTEM_PROMPT,
            output_type=RouterChoice,
        )
        result = None
        try:
            result = await agent.run(
                router_prompt(message, roster, recent),
                usage_limits=usage_limits_for(model, _ROUTER_USAGE_LIMITS),
                model_settings=_ROUTER_MODEL_SETTINGS,
            )
        finally:
            # Recorded on both paths from one place. A `finally` rather than a
            # matched pair of calls: the reservation has to be settled whatever
            # happened, and two call sites is how one of them drifts.
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
                result=result,
                status="COMPLETED" if result is not None else "FAILED",
                reservation=reservation,
                metadata={"helper": "agent_router"},
            )
        # Still through the tolerant parser: it is what turns "NONE", an
        # unknown name or a near-miss into silence rather than a guess.
        return resolve_router_choices(result.output.agents, roster)
    except RuntimeError, OSError, TimeoutError, ValueError:
        # Silence, not an error. The correct default answer to "who should reply
        # to this?" is nobody, so a router that cannot be reached must not stop
        # the message reaching the people in the room. pydantic-ai raises
        # `AgentRunError`/`UserError`, both `RuntimeError`s, and the transport
        # failures underneath are `OSError`/`TimeoutError` -- so this covers the
        # model being unreachable, misconfigured or over its limits.
        #
        # Deliberately not `Exception`: a `TypeError` here is our bug, and a
        # router that swallows its own bugs is one that silently stops routing.
        logger.error("agent.router.decision.failed", pod_id=pod_id, exc_info=True)
        return []


async def _resolve_runtime(*, organization_id: UUID | None, user_id: UUID):
    service = AgentRuntimeProfileService()
    try:
        return await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=agent_settings.agent_router_model,
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
    except RuntimeError:
        # Not in this deployment's catalog — the profile default will do.
        return await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
