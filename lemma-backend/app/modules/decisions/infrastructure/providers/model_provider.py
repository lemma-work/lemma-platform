"""Decisions answered by a language model: any one this deployment can run.

The model comes from the same resolution every one-shot call uses -- the system
model, else the workspace's -- so whatever provider an operator configured,
OpenAI- or Anthropic-compatible, hosted or local, answers decisions too.

Structured output alone is not a check. `StructuredDict` shows the model the
schema and hands back whatever dict it produced; nothing compares the two. The
output validator does, and on a background decision the model gets one chance
to correct itself. Someone waiting on an interactive one gets none: a second
round-trip costs more than an unavailable answer they can retry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.modules.decisions.domain.answers import (
    Answer,
    DecisionResult,
    DecisionUsage,
    answer_problems,
    answer_schema,
    to_value,
)
from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.request import DecisionCaller, DecisionTask
from app.modules.decisions.infrastructure.providers.model_prompt import build_prompt

if TYPE_CHECKING:
    from pydantic_ai import UsageLimits
    from pydantic_ai.models import Model


class ResolvedModel(Protocol):
    @property
    def model(self) -> Model: ...

    @property
    def runtime_profile(self) -> Mapping[str, object | None]: ...

    @property
    def usage_limits(self) -> UsageLimits: ...


class ModelResolver(Protocol):
    async def __call__(
        self,
        *,
        usage_limits: UsageLimits,
        user_id: UUID | None,
        organization_id: UUID | None,
        pod_id: UUID | None,
        model_name: str | None,
    ) -> ResolvedModel: ...


@dataclass(frozen=True, slots=True)
class _Budget:
    output_retries: int
    requests: int


_BUDGETS = {
    "interactive": _Budget(output_retries=0, requests=1),
    "background": _Budget(output_retries=1, requests=2),
}
#: Per request. The request caps (64 KiB evidence, 32 KiB of examples, a 16 KiB
#: schema and an 8,000-character instruction) stay inside it at a conservative
#: two bytes per token.
_INPUT_TOKENS_PER_REQUEST = 64_000
#: An answers object is small; the headroom is for models that reason first and
#: count that toward output.
_OUTPUT_TOKENS_PER_REQUEST = 8_000


class ModelDecisionProvider:
    def __init__(
        self, *, model_name: str | None, resolver: ModelResolver | None = None
    ) -> None:
        self._model_name = model_name
        self._resolver = resolver

    @property
    def name(self) -> str:
        return "model"

    async def decide(
        self, task: DecisionTask, *, caller: DecisionCaller, timeout_seconds: float
    ) -> DecisionResult:
        from pydantic_ai import (
            Agent,
            ModelRetry,
            UnexpectedModelBehavior,
            UsageLimitExceeded,
            UsageLimits,
        )
        from pydantic_ai.output import StructuredDict

        del timeout_seconds  # the service bounds the whole call
        budget = _BUDGETS[task.priority]
        resolve = self._resolver or _system_resolver()
        runtime = await resolve(
            usage_limits=UsageLimits(
                request_limit=budget.requests,
                input_tokens_limit=_INPUT_TOKENS_PER_REQUEST * budget.requests,
                output_tokens_limit=_OUTPUT_TOKENS_PER_REQUEST * budget.requests,
                count_tokens_before_request=True,
            ),
            user_id=caller.user_id,
            organization_id=caller.organization_id,
            pod_id=caller.pod_id,
            model_name=self._model_name,
        )
        prompt = build_prompt(task)
        agent = Agent(
            runtime.model,
            system_prompt=prompt.system,
            # A fresh schema every run: `StructuredDict` writes into the dict
            # it is given.
            output_type=StructuredDict(answer_schema(task.schema), name="answers"),
            retries={"output": budget.output_retries},
        )

        @agent.output_validator
        def _check(output: dict[str, object]) -> dict[str, object]:
            problems = answer_problems(task.schema, output)
            if problems:
                raise ModelRetry("Fix these answers: " + "; ".join(problems))
            return output

        try:
            result = await agent.run(prompt.user, usage_limits=runtime.usage_limits)
        except UsageLimitExceeded as exc:
            # A token or request ceiling, possibly hit before the model was
            # asked at all -- not an answer that failed to fit the questions.
            raise DecisionUnavailableError("token_limit") from exc
        except UnexpectedModelBehavior as exc:
            raise DecisionUnavailableError("invalid_output") from exc
        usage = result.usage
        return DecisionResult(
            answers={
                question.key: Answer(
                    to_value(question, result.output.get(question.key))
                )
                for question in task.schema.questions
            },
            provider=self.name,
            model=_model_name(runtime.runtime_profile),
            usage=DecisionUsage(
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
            ),
        )


def _system_resolver() -> ModelResolver:
    # Imported here: it loads the runtime profile service and the model
    # factories, which only a decision that is actually asked needs.
    from app.modules.agent.contracts.model_runtime import resolve_system_runtime

    return resolve_system_runtime


def _model_name(profile: Mapping[str, object | None]) -> str | None:
    name = profile.get("model_name") or profile.get("provider_model_name")
    return str(name) if name else None
