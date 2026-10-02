"""Defining pod deciders, versioning them, and testing a definition before it is saved."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from app.modules.decisions.domain.deciders import (
    DeciderDefinition,
    DeciderEntity,
    DeciderVersionEntity,
    check_decider_name,
)
from app.modules.decisions.domain.decisions import Answer
from app.modules.decisions.domain.errors import (
    DeciderExistsError,
    DeciderInvalidError,
    DeciderNotFoundError,
)
from app.modules.decisions.domain.questions import (
    AnswerValue,
    ChoiceQuestion,
    MultiChoiceQuestion,
)
from app.modules.decisions.infrastructure.repositories import SqlDeciderStore
from app.modules.decisions.infrastructure.rules_engine import check_expressions
from app.modules.decisions.services.decisions_service import Asker, DecisionsService
from app.modules.decisions.services.system_deciders import SYSTEM_PREFIX

#: Engines read every option's description on every call; past this many a
#: choice is better split into two questions.
_MANY_OPTIONS = 40


class SampleRow(BaseModel):
    """A sample state, and optionally the answers a person expects for it."""

    model_config = ConfigDict(extra="forbid")

    state: JsonValue
    expected: dict[str, AnswerValue] | None = None


@dataclass(frozen=True, slots=True)
class SampleDisagreement:
    row: int
    question: str
    expected: AnswerValue
    answer: Answer | None


@dataclass(frozen=True, slots=True)
class SampleResult:
    answers: list[dict[str, Answer]]
    open: list[list[str]]
    agreement: dict[str, tuple[int, int]]
    disagreements: list[SampleDisagreement]


class DecidersService:
    def __init__(self, *, store: SqlDeciderStore, decisions: DecisionsService) -> None:
        self._store = store
        self._decisions = decisions

    async def create(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        name: str,
        definition: DeciderDefinition,
    ) -> DeciderEntity:
        check_name(name)
        check_definition(definition)
        created = await self._store.create(
            pod_id=pod_id, user_id=user_id, name=name, definition=definition
        )
        if created is None:
            raise DeciderExistsError(name)
        return created

    async def update(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        name: str,
        definition: DeciderDefinition,
    ) -> DeciderEntity:
        """Save `definition` as the decider's next version. Old versions stay."""
        check_definition(definition)
        saved = await self._store.save_version(
            pod_id=pod_id, name=name, user_id=user_id, definition=definition
        )
        if saved is None:
            raise DeciderNotFoundError(name)
        return saved

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity:
        found = await self._store.get(pod_id=pod_id, name=name)
        if found is None:
            raise DeciderNotFoundError(name)
        return found

    async def list(self, *, pod_id: UUID, limit: int) -> list[DeciderEntity]:
        return await self._store.list(pod_id=pod_id, limit=limit)

    async def versions(
        self, *, pod_id: UUID, name: str, limit: int
    ) -> list[DeciderVersionEntity]:
        found = await self.get(pod_id=pod_id, name=name)
        return await self._store.versions(decider_id=found.id, limit=limit)

    async def delete(self, *, pod_id: UUID, name: str) -> None:
        if not await self._store.delete(pod_id=pod_id, name=name):
            raise DeciderNotFoundError(name)

    async def test(
        self,
        *,
        decider: str | None,
        definition: DeciderDefinition | None,
        rows: Sequence[SampleRow],
        asker: Asker,
    ) -> SampleResult:
        """Decide sample rows without recording anything, and compare with what was expected.

        This is both the preview a person sees before a decider is turned on and
        the regression check before a revised definition replaces the old one.
        """
        if definition is not None:
            check_definition(definition)
        results = await self._decisions.decide_rows(
            decider=decider,
            definition=definition,
            rows=[row.state for row in rows],
            asker=asker,
            record=False,
        )
        agreement: dict[str, tuple[int, int]] = {}
        disagreements: list[SampleDisagreement] = []
        for row, result in zip(rows, results.rows, strict=True):
            for key, expected in (row.expected or {}).items():
                answer = result.answers.get(key)
                agreed_before, total_before = agreement.get(key, (0, 0))
                agreed = answer is not None and _same(answer.value, expected)
                agreement[key] = (agreed_before + int(agreed), total_before + 1)
                if not agreed:
                    disagreements.append(
                        SampleDisagreement(
                            row=result.index,
                            question=key,
                            expected=expected,
                            answer=answer,
                        )
                    )
        return SampleResult(
            answers=[result.answers for result in results.rows],
            open=[result.open for result in results.rows],
            agreement=agreement,
            disagreements=disagreements,
        )


def check_name(name: str) -> None:
    if name.startswith(SYSTEM_PREFIX):
        raise DeciderInvalidError(
            "`system:` names belong to deciders that ship with Lemma."
        )
    try:
        check_decider_name(name)
    except ValueError as exc:
        raise DeciderInvalidError(str(exc)) from exc


def check_definition(definition: DeciderDefinition) -> None:
    try:
        check_expressions(definition.rules)
    except ValueError as exc:
        raise DeciderInvalidError(str(exc)) from exc


def warnings_for(definition: DeciderDefinition) -> list[str]:
    """Things that are allowed but usually a mistake, said back to whoever wrote them."""
    warnings: list[str] = []
    if definition.input.fields is None:
        warnings.append(
            "The input view names no fields, so every field of the state reaches the "
            "engines. List only the fields the questions need."
        )
    for key, question in definition.questions.items():
        if isinstance(question, ChoiceQuestion) and question.fallback is None:
            warnings.append(
                f"Question {key!r} has no fallback, so an unsure answer leaves it open "
                "rather than choosing a safe option."
            )
        if (
            isinstance(question, ChoiceQuestion | MultiChoiceQuestion)
            and question.options
        ):
            descriptions = [
                option.description.strip().lower()
                for option in question.options.values()
            ]
            if len(set(descriptions)) != len(descriptions):
                warnings.append(
                    f"Question {key!r} has options with the same description."
                )
            if len(question.options) > _MANY_OPTIONS:
                warnings.append(
                    f"Question {key!r} has {len(question.options)} options; consider "
                    "splitting it into two questions."
                )
    return warnings


def _same(value: AnswerValue, expected: AnswerValue) -> bool:
    if isinstance(value, list) and isinstance(expected, list):
        return sorted(value) == sorted(expected)
    return value == expected and type(value) is type(expected)
