"""The ladder: rules, then System One, then a model, until every question is answered.

Each rung answers what it can and passes the rest on. A rung that is not
available is skipped and recorded as such, so a deployment with no Typesafe key
gets the same behaviour with one rung fewer. The policy decides what an engine
answer must show to stand; a question still open at the top is the caller's
to put to a person.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.decisions.domain.deciders import DeciderDefinition, Lane, Policy
from app.modules.decisions.domain.decisions import (
    Answer,
    ExampleView,
    Rung,
    RungOutcome,
    RungTrace,
)
from app.modules.decisions.domain.ports import (
    Ask,
    Engine,
    EngineFailedError,
    EngineOutcome,
    EngineUnavailableError,
    Payer,
)
from app.modules.decisions.domain.questions import (
    ChoiceQuestion,
    MultiChoiceQuestion,
    Question,
    ScaleQuestion,
    YesNoQuestion,
)
from app.modules.decisions.infrastructure.rules_engine import answer_by_rules
from app.modules.decisions.services.rendering import RenderedState

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LadderResult:
    answers: dict[str, Answer]
    open: list[str]
    trace: list[RungTrace]


class Ladder:
    """Climbs the rungs above rules in the order given."""

    def __init__(self, engines: Sequence[Engine]) -> None:
        self._engines = tuple(engines)

    async def climb(
        self,
        *,
        definition: DeciderDefinition,
        questions: Mapping[str, Question],
        rendered: RenderedState,
        examples: Mapping[str, Sequence[ExampleView]],
        payer: Payer,
        lane: Lane,
        allow_third_party: bool = True,
    ) -> LadderResult:
        climb = _Climb(questions)
        if definition.rules:
            ruled = answer_by_rules(definition.rules, rendered.value, questions)
            climb.record(
                RungTrace(
                    rung=Rung.RULES,
                    outcome=RungOutcome.ANSWERED if ruled else RungOutcome.ABSTAINED,
                    questions=sorted(ruled),
                ),
                ruled,
            )
        for engine in self._engines:
            if not climb.open:
                break
            skip_reason = _skip_reason(
                engine,
                definition.policy,
                lane=lane,
                allow_third_party=allow_third_party,
                system_one_abstained=climb.system_one_abstained,
                payer=payer,
            )
            if skip_reason is not None:
                climb.record(
                    RungTrace(
                        rung=engine.rung, outcome=skip_reason, questions=climb.open
                    ),
                    {},
                )
                continue
            ask = Ask(
                questions={key: questions[key] for key in climb.open},
                evidence=rendered.text,
                guidance=definition.guidance,
                examples={key: examples.get(key, ()) for key in climb.open},
                lane=lane,
                payer=payer,
            )
            step, accepted = await _ask_rung(engine, ask, definition.policy, questions)
            climb.record(step, accepted)
        return climb.finish()


class _Climb:
    """What has been answered so far, what is still open, and how it got there."""

    def __init__(self, questions: Mapping[str, Question]) -> None:
        self._questions = questions
        self.answers: dict[str, Answer] = {}
        self.trace: list[RungTrace] = []
        self.open: list[str] = list(questions)
        self.system_one_abstained = False

    def record(self, step: RungTrace, accepted: Mapping[str, Answer]) -> None:
        self.answers.update(accepted)
        self.trace.append(step)
        before = self.open
        self.open = [key for key in before if key not in accepted]
        if (
            step.rung is Rung.SYSTEM_ONE
            and step.outcome in (RungOutcome.ANSWERED, RungOutcome.ABSTAINED)
            and self.open
        ):
            self.system_one_abstained = True

    def finish(self) -> LadderResult:
        """Give each open choice its fallback, marked as abstained."""
        last_rung = self.trace[-1].rung if self.trace else Rung.RULES
        for key in self.open:
            question = self._questions[key]
            if isinstance(question, ChoiceQuestion) and question.fallback is not None:
                self.answers[key] = Answer(
                    value=question.fallback, by=last_rung, abstained=True
                )
        return LadderResult(answers=self.answers, open=self.open, trace=self.trace)


async def _ask_rung(
    engine: Engine,
    ask: Ask,
    policy: Policy,
    questions: Mapping[str, Question],
) -> tuple[RungTrace, dict[str, Answer]]:
    """One rung's answers that stand, and the trace of asking it.

    A rung that is unavailable or fails did not answer: the decision goes on to
    the next rung, or ends open for a person, rather than failing whoever asked.
    """
    open_keys = list(ask.questions)
    started = time.monotonic()
    try:
        outcome = await engine.answer(ask)
    except EngineUnavailableError:
        return (
            RungTrace(
                rung=engine.rung,
                outcome=RungOutcome.UNAVAILABLE,
                questions=open_keys,
                latency_ms=_elapsed_ms(started),
            ),
            {},
        )
    except EngineFailedError:
        logger.error(
            "decisions.ladder.rung_failed.degraded",
            rung=engine.rung.value,
            exc_info=True,
        )
        return (
            RungTrace(
                rung=engine.rung,
                outcome=RungOutcome.FAILED,
                questions=open_keys,
                latency_ms=_elapsed_ms(started),
            ),
            {},
        )
    accepted = accept(outcome, policy, questions, engine.rung)
    return (
        RungTrace(
            rung=engine.rung,
            outcome=RungOutcome.ANSWERED if accepted else RungOutcome.ABSTAINED,
            questions=sorted(accepted),
            model=outcome.model,
            latency_ms=_elapsed_ms(started),
            input_tokens=outcome.input_tokens,
        ),
        accepted,
    )


def _skip_reason(
    engine: Engine,
    policy: Policy,
    *,
    lane: Lane,
    allow_third_party: bool,
    system_one_abstained: bool,
    payer: Payer,
) -> RungOutcome | None:
    if engine.rung is Rung.SYSTEM_ONE and not allow_third_party:
        return RungOutcome.SKIPPED
    if engine.rung is Rung.MODEL and system_one_abstained:
        # Interactive lanes have no time for a second opinion, and a policy
        # can decline one; either way the question stays open for a person.
        if not policy.escalate_to_model or lane is Lane.INTERACTIVE:
            return RungOutcome.SKIPPED
    if not engine.is_available(organization_id=payer.organization_id):
        return RungOutcome.NOT_CONFIGURED
    return None


def accept(
    outcome: EngineOutcome,
    policy: Policy,
    questions: Mapping[str, Question],
    rung: Rung,
) -> dict[str, Answer]:
    """The engine answers that stand under the policy."""
    accepted: dict[str, Answer] = {}
    for key, answer in outcome.answers.items():
        question = questions.get(key)
        if question is None or key in outcome.abstained:
            continue
        if _rules_only(policy, key, answer):
            continue
        if not _meets_required_confidence(policy, key, question, answer):
            continue
        if rung is Rung.SYSTEM_ONE and not _concentrated(policy, question, answer):
            continue
        accepted[key] = answer
    return accepted


def _rules_only(policy: Policy, key: str, answer: Answer) -> bool:
    reserved = set(policy.rules_only.get(key, ()))
    if not reserved:
        return False
    values = answer.value if isinstance(answer.value, list) else [answer.value]
    return any(_policy_key(value) in reserved for value in values)


def _meets_required_confidence(
    policy: Policy, key: str, question: Question, answer: Answer
) -> bool:
    required = policy.require_confidence.get(key, {})
    if isinstance(answer.value, list):
        return True
    threshold = required.get(_policy_key(answer.value))
    if threshold is None:
        return True
    strength = _strength(question, answer)
    return strength is not None and strength >= threshold


def _concentrated(policy: Policy, question: Question, answer: Answer) -> bool:
    """Whether System One committed: its own confidence, or a probability outside the band."""
    low, high = policy.yes_no_band
    match question:
        case ChoiceQuestion() | ScaleQuestion():
            return (
                answer.confidence is None or answer.confidence >= policy.abstain_below
            )
        case YesNoQuestion():
            p_yes = (answer.distribution or {}).get("yes")
            return p_yes is None or not low < p_yes < high
        case MultiChoiceQuestion():
            return all(not low < p < high for p in (answer.distribution or {}).values())
    return True


def _strength(question: Question, answer: Answer) -> float | None:
    if isinstance(question, YesNoQuestion):
        p_yes = (answer.distribution or {}).get("yes")
        return None if p_yes is None else max(p_yes, 1 - p_yes)
    return answer.confidence


def _policy_key(value: str | bool | int) -> str:
    """How an answer is named in a policy: option keys as-is, booleans and levels as text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
