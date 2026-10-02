"""Asking, recording and answering decisions.

The one door every caller goes through: platform code by the contract, the API,
the agent tools. Reads and writes are short units of work opened by the stores;
engines are called between them, never inside one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.core.log.log import get_logger
from app.modules.decisions.config import DecisionsSettings, decisions_settings
from app.modules.decisions.domain.deciders import (
    DeciderDefinition,
    DeciderScope,
    Lane,
    ResolvedDecider,
)
from app.modules.decisions.domain.decisions import (
    Answer,
    DecisionEntity,
    DecisionStatus,
    ExampleEntity,
    ExampleSource,
    ExampleView,
    Rung,
    check_against_shape,
    shape_of,
    subject_owner,
)
from app.modules.decisions.domain.errors import (
    DeciderNotFoundError,
    DecisionAnswerInvalidError,
    DecisionNotFoundError,
    DecisionRequestInvalidError,
    DecisionRowsTooManyError,
)
from app.modules.decisions.domain.ports import Payer
from app.modules.decisions.domain.questions import (
    AnswerValue,
    Option,
    Question,
    require_answerable,
    with_options,
)
from app.modules.decisions.infrastructure.repositories import (
    SqlDeciderStore,
    SqlDecisionStore,
    SqlExampleStore,
)
from app.modules.decisions.services.ladder import Ladder
from app.modules.decisions.services.rendering import render
from app.modules.decisions.services.system_deciders import SYSTEM_PREFIX, system_decider

logger = get_logger(__name__)

EXAMPLES_PER_QUESTION = 6


@dataclass(frozen=True, slots=True)
class Asker:
    """Who is asking, on whose behalf, and who may see the record.

    `visibility` is PERSONAL when the state is one person's own -- their inbox,
    their conversation -- and POD when the pod's members all may see it.
    """

    user_id: UUID | None
    pod_id: UUID | None
    organization_id: UUID | None = None
    visibility: str = "POD"
    allow_third_party: bool = True


class DecideRequest(BaseModel):
    """One state and the decider to ask about it."""

    model_config = ConfigDict(extra="forbid")

    decider: str | None = Field(
        default=None,
        description="A pod decider's name, or `system:<name>` for one that ships with Lemma.",
    )
    definition: DeciderDefinition | None = Field(
        default=None, description="Questions asked inline, without a named decider."
    )
    state: JsonValue
    subject: str | None = Field(default=None, max_length=512)
    options: dict[str, dict[str, Option]] = Field(default_factory=dict)
    lane: Lane | None = None
    record: bool = True


@dataclass(frozen=True, slots=True)
class RowResult:
    index: int
    row_id: str | None
    answers: dict[str, Answer]
    open: list[str]
    decision_id: UUID | None = None
    failed: bool = False


@dataclass(frozen=True, slots=True)
class RowsResult:
    decider_key: str
    rows: list[RowResult]
    counts: dict[str, dict[str, int]]
    failed: int = 0
    rungs: dict[str, int] = field(default_factory=dict)


class DecisionsService:
    def __init__(
        self,
        *,
        deciders: SqlDeciderStore,
        decisions: SqlDecisionStore,
        examples: SqlExampleStore,
        ladder: Ladder,
        settings: DecisionsSettings = decisions_settings,
    ) -> None:
        self._deciders = deciders
        self._decisions = decisions
        self._examples = examples
        self._ladder = ladder
        self._settings = settings

    async def resolve(
        self,
        *,
        pod_id: UUID | None,
        decider: str | None,
        definition: DeciderDefinition | None,
    ) -> ResolvedDecider:
        if (decider is None) == (definition is None):
            raise DecisionRequestInvalidError(
                "Name a decider or pass a definition, not both and not neither."
            )
        if definition is not None:
            return ResolvedDecider(
                scope=DeciderScope.INLINE,
                key=inline_key(definition),
                name=None,
                version=None,
                definition=definition,
            )
        assert decider is not None
        if decider.startswith(SYSTEM_PREFIX):
            found = system_decider(decider)
            if found is None:
                raise DeciderNotFoundError(decider)
            return ResolvedDecider(
                scope=DeciderScope.SYSTEM,
                key=decider,
                name=decider,
                version=None,
                definition=found,
            )
        if pod_id is None:
            raise DeciderNotFoundError(decider)
        entity = await self._deciders.get(pod_id=pod_id, name=decider)
        if entity is None:
            raise DeciderNotFoundError(decider)
        return ResolvedDecider(
            scope=DeciderScope.POD,
            key=entity.name,
            name=entity.name,
            version=entity.version,
            definition=entity.definition,
        )

    async def decide(self, request: DecideRequest, asker: Asker) -> DecisionEntity:
        resolved = await self.resolve(
            pod_id=asker.pod_id, decider=request.decider, definition=request.definition
        )
        questions = asked_questions(resolved.definition, request.options)
        existing = None
        if request.subject is not None and request.record:
            existing = await self._recorded(resolved, request.subject, asker)
            if existing is not None and not existing.interrupted:
                return existing
        examples = await self._examples_for(resolved, questions, asker)
        decision = await self._ask(
            resolved,
            questions,
            request.state,
            request.subject,
            request.lane,
            examples,
            asker,
        )
        if not request.record:
            return decision
        stored = await self._keep(decision, existing, asker)
        logger.info(
            "decisions.decisions_service.decided.observed",
            decider_scope=resolved.scope.value,
            status=stored.status.value,
            rungs=",".join(step.rung.value for step in stored.trace),
        )
        return stored

    async def decide_rows(
        self,
        *,
        decider: str | None,
        definition: DeciderDefinition | None,
        rows: Sequence[JsonValue],
        asker: Asker,
        options: Mapping[str, dict[str, Option]] | None = None,
        id_field: str | None = None,
        subject_prefix: str | None = None,
        record: bool = True,
    ) -> RowsResult:
        """Decide every row with one definition, in parallel within the lane's budget."""
        if len(rows) > self._settings.decisions_max_rows:
            raise DecisionRowsTooManyError(self._settings.decisions_max_rows)
        resolved = await self.resolve(
            pod_id=asker.pod_id, decider=decider, definition=definition
        )
        questions = asked_questions(resolved.definition, dict(options or {}))
        examples = await self._examples_for(resolved, questions, asker)
        limit = asyncio.Semaphore(self._settings.decisions_row_concurrency)

        async def one(index: int, row: JsonValue) -> RowResult:
            row_id = _row_id(row, id_field, index)
            subject = (
                f"{subject_prefix}:{row_id}" if subject_prefix and row_id else None
            )
            async with limit:
                existing = None
                if record and subject is not None:
                    existing = await self._recorded(resolved, subject, asker)
                    if existing is not None and not existing.interrupted:
                        return RowResult(
                            index, row_id, existing.answers, existing.open, existing.id
                        )
                decision = await self._ask(
                    resolved, questions, row, subject, Lane.BULK, examples, asker
                )
                if record:
                    decision = await self._keep(decision, existing, asker)
            return RowResult(
                index,
                row_id,
                decision.answers,
                decision.open,
                decision.id if record else None,
            )

        outcomes = await asyncio.gather(
            *(one(index, row) for index, row in enumerate(rows)), return_exceptions=True
        )
        results: list[RowResult] = []
        failures: list[BaseException] = []
        for index, outcome in enumerate(outcomes):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                failures.append(outcome)
                results.append(
                    RowResult(
                        index,
                        _row_id(rows[index], id_field, index),
                        {},
                        list(questions),
                        failed=True,
                    )
                )
            else:
                results.append(outcome)
        if failures:
            # One line per batch, not per row: a provider outage fails every row
            # the same way and would otherwise drown the log.
            logger.error(
                "decisions.decisions_service.rows_failed.degraded",
                failed=len(failures),
                total=len(rows),
                exc_info=failures[0],
            )
        return RowsResult(
            decider_key=resolved.key,
            rows=results,
            counts=count_answers(results),
            failed=len(failures),
        )

    async def get(
        self, *, decision_id: UUID, pod_id: UUID, viewer_id: UUID
    ) -> DecisionEntity:
        decision = await self._decisions.get(decision_id)
        if decision is None or not decision.visible_to(
            pod_id=pod_id, viewer_id=viewer_id
        ):
            raise DecisionNotFoundError()
        return decision

    async def list(
        self,
        *,
        pod_id: UUID,
        viewer_id: UUID,
        decider: str | None,
        open_only: bool,
        before: datetime | None,
        limit: int,
    ) -> list[DecisionEntity]:
        return await self._decisions.list(
            pod_id=pod_id,
            viewer_id=viewer_id,
            decider_key=decider,
            open_only=open_only,
            before=before,
            limit=limit,
        )

    async def answer(
        self,
        *,
        decision_id: UUID,
        pod_id: UUID,
        answers: Mapping[str, AnswerValue],
        by: Rung,
        user_id: UUID,
    ) -> DecisionEntity:
        """Record a person's or an agent's answer; a person's becomes an example.

        Only a person teaches. An agent that works out an open question by
        investigating is recorded as the agent, and its answer never becomes an
        example: a machine must not be able to teach itself.
        """
        if by not in (Rung.PERSON, Rung.AGENT):
            raise DecisionAnswerInvalidError(
                "Only a person or an agent answers a decision."
            )
        decision = await self.get(
            decision_id=decision_id, pod_id=pod_id, viewer_id=user_id
        )
        checked = checked_answers(decision, answers)
        updated = answered(decision, checked, by=by, user_id=user_id)
        await self._decisions.save_answers(updated)
        if by is Rung.PERSON and decision.evidence:
            await self._examples.add(
                [
                    ExampleEntity(
                        pod_id=decision.pod_id,
                        decider_key=decision.decider_key,
                        question_key=key,
                        value=value,
                        evidence=decision.evidence,
                        source=ExampleSource.PERSON,
                        visibility=decision.visibility,
                        user_id=user_id,
                        decision_id=decision.id,
                    )
                    for key, value in checked.items()
                ]
            )
        return updated

    async def _recorded(
        self, resolved: ResolvedDecider, subject: str, asker: Asker
    ) -> DecisionEntity | None:
        """The decision already kept for this subject, if the asker may read it."""
        found = await self._decisions.find_by_subject(
            pod_id=asker.pod_id,
            decider_key=resolved.key,
            subject_key=subject,
            owner_id=subject_owner(visibility=asker.visibility, user_id=asker.user_id),
        )
        return found if _readable_by(found, asker) else None

    async def _keep(
        self,
        decision: DecisionEntity,
        interrupted: DecisionEntity | None,
        asker: Asker,
    ) -> DecisionEntity:
        """Keep a new decision, or overwrite the interrupted one it replaces.

        Losing an insert race returns the winner's record, which is the
        asker's own or the pod's by the subject namespace. The check after is
        the boundary should that ever not hold: the asker gets the answer just
        made, unrecorded, rather than somebody else's evidence.
        """
        if interrupted is None:
            kept = await self._decisions.insert(decision)
        else:
            kept = await self._decisions.save_reasked(
                decision.model_copy(
                    update={"id": interrupted.id, "created_at": interrupted.created_at}
                )
            )
        if _readable_by(kept, asker):
            return kept
        logger.warning(
            "decisions.decisions_service.unreadable_record.degraded",
            decider_key=decision.decider_key,
        )
        return decision

    async def _examples_for(
        self,
        resolved: ResolvedDecider,
        questions: Mapping[str, Question],
        asker: Asker,
    ) -> Mapping[str, Sequence[ExampleView]]:
        if resolved.scope is DeciderScope.INLINE or asker.pod_id is None:
            return {}
        return await self._examples.recent(
            pod_id=asker.pod_id,
            decider_key=resolved.key,
            question_keys=list(questions),
            per_question=EXAMPLES_PER_QUESTION,
            viewer_id=asker.user_id,
        )

    async def _ask(
        self,
        resolved: ResolvedDecider,
        questions: Mapping[str, Question],
        state: JsonValue,
        subject: str | None,
        lane: Lane | None,
        examples: Mapping[str, Sequence[ExampleView]],
        asker: Asker,
    ) -> DecisionEntity:
        rendered = render(state, resolved.definition.input)
        result = await self._ladder.climb(
            definition=resolved.definition,
            questions=questions,
            rendered=rendered,
            examples=examples,
            payer=Payer(
                user_id=asker.user_id,
                organization_id=asker.organization_id,
                pod_id=asker.pod_id,
                source_id=resolved.key,
            ),
            lane=lane or resolved.definition.policy.lane,
            allow_third_party=asker.allow_third_party,
        )
        now = datetime.now(timezone.utc)
        return DecisionEntity(
            pod_id=asker.pod_id,
            organization_id=asker.organization_id,
            user_id=asker.user_id,
            visibility=asker.visibility,
            decider_scope=resolved.scope,
            decider_key=resolved.key,
            decider_name=resolved.name,
            decider_version=resolved.version,
            subject_key=subject,
            shape={key: shape_of(question) for key, question in questions.items()},
            answers=result.answers,
            open=result.open,
            status=DecisionStatus.ABSTAINED if result.open else DecisionStatus.ANSWERED,
            trace=result.trace,
            evidence=rendered.text,
            evidence_expires_at=now
            + timedelta(days=self._settings.decision_evidence_ttl_days),
            created_at=now,
            updated_at=now,
        )


def checked_answers(
    decision: DecisionEntity, answers: Mapping[str, AnswerValue]
) -> dict[str, AnswerValue]:
    """The answers, each checked against the question as it was asked."""
    if not answers:
        raise DecisionAnswerInvalidError("Answer at least one question.")
    checked: dict[str, AnswerValue] = {}
    for key, value in answers.items():
        shape = decision.shape.get(key)
        if shape is None:
            raise DecisionAnswerInvalidError(f"The decision has no question {key!r}.")
        try:
            checked[key] = check_against_shape(shape, value)
        except ValueError as exc:
            raise DecisionAnswerInvalidError(f"Question {key!r}: {exc}") from exc
    return checked


def answered(
    decision: DecisionEntity,
    checked: Mapping[str, AnswerValue],
    *,
    by: Rung,
    user_id: UUID,
) -> DecisionEntity:
    """The decision after these answers: corrected if any changed a machine's answer."""
    corrected = any(
        key not in decision.open
        and key in decision.answers
        and decision.answers[key].value != value
        for key, value in checked.items()
    )
    still_open = [key for key in decision.open if key not in checked]
    if corrected:
        status = DecisionStatus.CORRECTED
    elif decision.open and not still_open:
        status = DecisionStatus.RESOLVED
    else:
        status = decision.status
    return decision.model_copy(
        update={
            "answers": {
                **decision.answers,
                **{key: Answer(value=value, by=by) for key, value in checked.items()},
            },
            "open": still_open,
            "status": status,
            "answered_by_user_id": user_id,
            "answered_at": datetime.now(timezone.utc),
        }
    )


def asked_questions(
    definition: DeciderDefinition, options: Mapping[str, dict[str, Option]]
) -> dict[str, Question]:
    """The definition's questions with the caller's options added, ready to ask."""
    unknown = sorted(set(options) - set(definition.questions))
    if unknown:
        raise DecisionRequestInvalidError(
            f"Options were passed for unknown questions {unknown}."
        )
    questions: dict[str, Question] = {}
    for key, question in definition.questions.items():
        try:
            merged = with_options(question, options.get(key))
            require_answerable(merged)
        except ValueError as exc:
            raise DecisionRequestInvalidError(f"Question {key!r}: {exc}") from exc
        questions[key] = merged
    return questions


def inline_key(definition: DeciderDefinition) -> str:
    """The key an inline definition is filed under: a digest of what it asks."""
    canonical = json.dumps(
        definition.model_dump(mode="json", exclude_none=True), sort_keys=True
    )
    return "inline:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _readable_by(decision: DecisionEntity | None, asker: Asker) -> bool:
    return decision is not None and decision.visible_to(
        pod_id=asker.pod_id, viewer_id=asker.user_id
    )


def count_answers(results: Sequence[RowResult]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for result in results:
        for key, answer in result.answers.items():
            values = answer.value if isinstance(answer.value, list) else [answer.value]
            for value in values:
                label = json.dumps(value) if not isinstance(value, str) else value
                bucket = counts.setdefault(key, {})
                bucket[label] = bucket.get(label, 0) + 1
    return counts


def _row_id(row: JsonValue, id_field: str | None, index: int) -> str | None:
    if id_field is None:
        return str(index + 1)
    if isinstance(row, dict):
        value = row.get(id_field)
        if isinstance(value, str | int) and not isinstance(value, bool):
            return str(value)
    return None
