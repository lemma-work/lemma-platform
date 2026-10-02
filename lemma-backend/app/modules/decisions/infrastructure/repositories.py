"""Where deciders, decisions and examples are kept.

Every method opens its own short unit of work and commits before returning:
the ladder calls engines between these reads and writes, and a connection
must never be held across that (see `lemma-backend/docs/development.md`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.decisions.domain.deciders import (
    DeciderDefinition,
    DeciderEntity,
    DeciderScope,
    DeciderVersionEntity,
)
from app.modules.decisions.domain.decisions import (
    Answer,
    DecisionEntity,
    DecisionStatus,
    ExampleEntity,
    ExampleSource,
    ExampleView,
    QuestionShape,
    RungTrace,
)
from app.modules.decisions.domain.questions import AnswerValue
from app.modules.decisions.infrastructure.models import (
    DeciderModel,
    DeciderVersionModel,
    DecisionExampleModel,
    DecisionModel,
)

_ANSWERS = TypeAdapter(dict[str, Answer])
_SHAPE = TypeAdapter(dict[str, QuestionShape])
_TRACE = TypeAdapter(list[RungTrace])
_ANSWER_VALUE: TypeAdapter[AnswerValue] = TypeAdapter(AnswerValue)


def _definition_json(definition: DeciderDefinition) -> dict[str, JsonValue]:
    return definition.model_dump(mode="json", exclude_none=True)


def _decider(row: DeciderModel) -> DeciderEntity:
    return DeciderEntity(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        pod_id=row.pod_id,
        user_id=row.user_id,
        name=row.name,
        visibility=row.visibility,
        version=row.version,
        definition=DeciderDefinition.model_validate(row.definition),
    )


class SqlDeciderStore:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(DeciderModel).where(
                    DeciderModel.pod_id == pod_id, DeciderModel.name == name
                )
            )
            return None if row is None else _decider(row)

    async def list(self, *, pod_id: UUID, limit: int) -> list[DeciderEntity]:
        async with self._uow_factory() as uow:
            rows = await uow.session.scalars(
                select(DeciderModel)
                .where(DeciderModel.pod_id == pod_id)
                .order_by(DeciderModel.name)
                .limit(limit)
            )
            return [_decider(row) for row in rows]

    async def create(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        name: str,
        definition: DeciderDefinition,
    ) -> DeciderEntity | None:
        """The new decider, or None when the name is taken in this pod."""
        async with self._uow_factory() as uow:
            inserted = await uow.session.scalar(
                insert(DeciderModel)
                .values(
                    pod_id=pod_id,
                    user_id=user_id,
                    name=name,
                    version=1,
                    definition=_definition_json(definition),
                )
                .on_conflict_do_nothing(constraint="uq_deciders_pod_name")
                .returning(DeciderModel.id)
            )
            if inserted is None:
                return None
            uow.session.add(
                DeciderVersionModel(
                    decider_id=inserted,
                    version=1,
                    definition=_definition_json(definition),
                    created_by=user_id,
                )
            )
            await uow.session.flush()
            row = await uow.session.get_one(DeciderModel, inserted)
            return _decider(row)

    async def save_version(
        self,
        *,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        definition: DeciderDefinition,
    ) -> DeciderEntity | None:
        """The decider at its next version, or None if it no longer exists."""
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(DeciderModel)
                .where(DeciderModel.pod_id == pod_id, DeciderModel.name == name)
                .with_for_update()
            )
            if row is None:
                return None
            row.version += 1
            row.definition = _definition_json(definition)
            row.updated_at = datetime.now(timezone.utc)
            uow.session.add(
                DeciderVersionModel(
                    decider_id=row.id,
                    version=row.version,
                    definition=_definition_json(definition),
                    created_by=user_id,
                )
            )
            await uow.session.flush()
            return _decider(row)

    async def versions(
        self, *, decider_id: UUID, limit: int
    ) -> list[DeciderVersionEntity]:
        async with self._uow_factory() as uow:
            rows = await uow.session.scalars(
                select(DeciderVersionModel)
                .where(DeciderVersionModel.decider_id == decider_id)
                .order_by(DeciderVersionModel.version.desc())
                .limit(limit)
            )
            return [
                DeciderVersionEntity(
                    decider_id=row.decider_id,
                    version=row.version,
                    definition=DeciderDefinition.model_validate(row.definition),
                    created_by=row.created_by,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def version(
        self, *, pod_id: UUID, name: str, version: int
    ) -> DeciderDefinition | None:
        async with self._uow_factory() as uow:
            definition = await uow.session.scalar(
                select(DeciderVersionModel.definition)
                .join(DeciderModel, DeciderModel.id == DeciderVersionModel.decider_id)
                .where(
                    DeciderModel.pod_id == pod_id,
                    DeciderModel.name == name,
                    DeciderVersionModel.version == version,
                )
            )
            return (
                None
                if definition is None
                else DeciderDefinition.model_validate(definition)
            )

    async def delete(self, *, pod_id: UUID, name: str) -> bool:
        """Delete the decider, its versions and its examples; its decisions stay."""
        async with self._uow_factory() as uow:
            deleted = await uow.session.scalar(
                delete(DeciderModel)
                .where(DeciderModel.pod_id == pod_id, DeciderModel.name == name)
                .returning(DeciderModel.id)
            )
            if deleted is None:
                return False
            await uow.session.execute(
                delete(DecisionExampleModel).where(
                    DecisionExampleModel.pod_id == pod_id,
                    DecisionExampleModel.decider_key == name,
                )
            )
            return True


def _decision(row: DecisionModel) -> DecisionEntity:
    return DecisionEntity(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        pod_id=row.pod_id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        visibility=row.visibility,
        decider_scope=DeciderScope(row.decider_scope),
        decider_key=row.decider_key,
        decider_name=row.decider_name,
        decider_version=row.decider_version,
        subject_key=row.subject_key,
        shape=_SHAPE.validate_python(row.shape),
        answers=_ANSWERS.validate_python(row.answers),
        open=list(row.open),
        status=DecisionStatus(row.status),
        trace=_TRACE.validate_python(row.trace),
        evidence=row.evidence,
        evidence_expires_at=row.evidence_expires_at,
        answered_by_user_id=row.answered_by_user_id,
        answered_at=row.answered_at,
    )


def _answers_json(answers: Mapping[str, Answer]) -> dict[str, JsonValue]:
    return {
        key: answer.model_dump(mode="json", exclude_none=True)
        for key, answer in answers.items()
    }


class SqlDecisionStore:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def find_by_subject(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        subject_key: str,
        owner_id: UUID | None,
    ) -> DecisionEntity | None:
        """The decision filed under this subject in `owner_id`'s namespace.

        `owner_id` is the asker for a PERSONAL decision and None for a POD one
        (`subject_owner`), so a person only ever finds their own private record
        or the pod's shared one.
        """
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(DecisionModel).where(
                    DecisionModel.pod_id.is_(None)
                    if pod_id is None
                    else DecisionModel.pod_id == pod_id,
                    DecisionModel.decider_key == decider_key,
                    DecisionModel.subject_key == subject_key,
                    DecisionModel.subject_owner_id.is_(None)
                    if owner_id is None
                    else DecisionModel.subject_owner_id == owner_id,
                )
            )
            return None if row is None else _decision(row)

    async def insert(self, decision: DecisionEntity) -> DecisionEntity:
        """Keep `decision`, or return the one already kept for its subject.

        Two workers deciding the same redelivered event race here; the loser
        reads the winner's answer instead of keeping a second, possibly
        different, one.
        """
        async with self._uow_factory() as uow:
            statement = (
                insert(DecisionModel)
                .values(
                    id=decision.id,
                    created_at=decision.created_at,
                    updated_at=decision.updated_at,
                    pod_id=decision.pod_id,
                    organization_id=decision.organization_id,
                    user_id=decision.user_id,
                    visibility=decision.visibility,
                    decider_scope=decision.decider_scope.value,
                    decider_key=decision.decider_key,
                    decider_name=decision.decider_name,
                    decider_version=decision.decider_version,
                    subject_key=decision.subject_key,
                    subject_owner_id=decision.subject_owner_id,
                    shape={
                        key: shape.model_dump(mode="json", exclude_none=True)
                        for key, shape in decision.shape.items()
                    },
                    answers=_answers_json(decision.answers),
                    open=list(decision.open),
                    status=decision.status.value,
                    trace=[
                        step.model_dump(mode="json", exclude_none=True)
                        for step in decision.trace
                    ],
                    evidence=decision.evidence,
                    evidence_expires_at=decision.evidence_expires_at,
                )
                .returning(DecisionModel.id)
            )
            if decision.subject_key is not None:
                statement = statement.on_conflict_do_nothing()
            inserted = await uow.session.scalar(statement)
            if inserted is not None or decision.subject_key is None:
                return decision
        existing = await self.find_by_subject(
            pod_id=decision.pod_id,
            decider_key=decision.decider_key,
            subject_key=decision.subject_key,
            owner_id=decision.subject_owner_id,
        )
        return existing or decision

    async def get(self, decision_id: UUID) -> DecisionEntity | None:
        async with self._uow_factory() as uow:
            row = await uow.session.get(DecisionModel, decision_id)
            return None if row is None else _decision(row)

    async def list(
        self,
        *,
        pod_id: UUID,
        viewer_id: UUID,
        decider_key: str | None,
        open_only: bool,
        before: datetime | None,
        limit: int,
    ) -> list[DecisionEntity]:
        """Newest first: the pod's shared decisions and the viewer's own."""
        conditions = [
            DecisionModel.pod_id == pod_id,
            or_(DecisionModel.visibility == "POD", DecisionModel.user_id == viewer_id),
        ]
        if decider_key is not None:
            conditions.append(DecisionModel.decider_key == decider_key)
        if open_only:
            conditions.append(func.jsonb_array_length(DecisionModel.open) > 0)
        if before is not None:
            conditions.append(DecisionModel.created_at < before)
        async with self._uow_factory() as uow:
            rows = await uow.session.scalars(
                select(DecisionModel)
                .where(and_(*conditions))
                .order_by(DecisionModel.created_at.desc())
                .limit(limit)
            )
            return [_decision(row) for row in rows]

    async def save_answers(self, decision: DecisionEntity) -> DecisionEntity:
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(DecisionModel)
                .where(DecisionModel.id == decision.id)
                .values(
                    answers=_answers_json(decision.answers),
                    open=list(decision.open),
                    status=decision.status.value,
                    answered_by_user_id=decision.answered_by_user_id,
                    answered_at=decision.answered_at,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return decision

    async def save_reasked(self, decision: DecisionEntity) -> DecisionEntity:
        """Overwrite an interrupted decision with a fresh ask's result, keeping its id.

        Guarded on the row still being unanswered, so a person's answer that
        landed in between is never overwritten by a machine's.
        """
        async with self._uow_factory() as uow:
            updated = await uow.session.scalar(
                update(DecisionModel)
                .where(
                    DecisionModel.id == decision.id,
                    DecisionModel.answered_by_user_id.is_(None),
                )
                .values(
                    decider_version=decision.decider_version,
                    shape={
                        key: shape.model_dump(mode="json", exclude_none=True)
                        for key, shape in decision.shape.items()
                    },
                    answers=_answers_json(decision.answers),
                    open=list(decision.open),
                    status=decision.status.value,
                    trace=[
                        step.model_dump(mode="json", exclude_none=True)
                        for step in decision.trace
                    ],
                    evidence=decision.evidence,
                    evidence_expires_at=decision.evidence_expires_at,
                    updated_at=datetime.now(timezone.utc),
                )
                .returning(DecisionModel.id)
            )
        if updated is None:
            current = await self.get(decision.id)
            return current or decision
        return decision

    async def forget_expired_evidence(self, *, now: datetime, limit: int) -> int:
        """Drop the evidence of decisions past their correction window."""
        async with self._uow_factory() as uow:
            expired = (
                select(DecisionModel.id)
                .where(
                    DecisionModel.evidence_expires_at.is_not(None),
                    DecisionModel.evidence_expires_at < now,
                )
                .limit(limit)
                .scalar_subquery()
            )
            result = await uow.session.execute(
                update(DecisionModel)
                .where(DecisionModel.id.in_(expired))
                .values(evidence=None, evidence_expires_at=None)
                .returning(DecisionModel.id)
            )
            return len(result.all())


class SqlExampleStore:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def recent(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        question_keys: Sequence[str],
        per_question: int,
        viewer_id: UUID | None,
    ) -> dict[str, list[ExampleView]]:
        """The newest examples `viewer_id` may read: the pod's, and their own private ones.

        Examples reach an engine's prompt, so a PERSONAL one is a copy of
        private evidence and is shown only to the person it belongs to.
        """
        if not question_keys or per_question <= 0:
            return {}
        readable = (
            DecisionExampleModel.visibility == "POD"
            if viewer_id is None
            else or_(
                DecisionExampleModel.visibility == "POD",
                DecisionExampleModel.user_id == viewer_id,
            )
        )
        examples: dict[str, list[ExampleView]] = {}
        async with self._uow_factory() as uow:
            for question_key in question_keys:
                rows = await uow.session.execute(
                    select(DecisionExampleModel.evidence, DecisionExampleModel.value)
                    .where(
                        DecisionExampleModel.pod_id.is_(None)
                        if pod_id is None
                        else DecisionExampleModel.pod_id == pod_id,
                        DecisionExampleModel.decider_key == decider_key,
                        DecisionExampleModel.question_key == question_key,
                        readable,
                    )
                    .order_by(DecisionExampleModel.created_at.desc())
                    .limit(per_question)
                )
                found = [
                    ExampleView(
                        evidence=evidence, value=_ANSWER_VALUE.validate_python(value)
                    )
                    for evidence, value in rows.all()
                ]
                if found:
                    examples[question_key] = found
        return examples

    async def add(self, examples: Sequence[ExampleEntity]) -> None:
        if not examples:
            return
        async with self._uow_factory() as uow:
            for example in examples:
                uow.session.add(
                    DecisionExampleModel(
                        id=example.id,
                        created_at=example.created_at,
                        pod_id=example.pod_id,
                        decider_key=example.decider_key,
                        question_key=example.question_key,
                        value=example.value,
                        evidence=example.evidence,
                        source=example.source.value,
                        visibility=example.visibility,
                        user_id=example.user_id,
                        decision_id=example.decision_id,
                    )
                )

    async def count_by_source(
        self, *, pod_id: UUID, decider_key: str
    ) -> dict[ExampleSource, int]:
        async with self._uow_factory() as uow:
            rows = await uow.session.execute(
                select(DecisionExampleModel.source, func.count())
                .where(
                    DecisionExampleModel.pod_id == pod_id,
                    DecisionExampleModel.decider_key == decider_key,
                )
                .group_by(DecisionExampleModel.source)
            )
            return {ExampleSource(source): count for source, count in rows.all()}
