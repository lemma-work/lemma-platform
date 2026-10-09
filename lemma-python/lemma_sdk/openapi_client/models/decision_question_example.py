from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="DecisionQuestionExample")


@_attrs_define
class DecisionQuestionExample:
    """A past case and how it should have been answered.

    Attributes:
        answer (bool | int | None | str): How that case was answered: one of the question's values, or null when the
            right answer there was 'can't tell'.
        evidence (Any):
    """

    answer: bool | int | None | str
    evidence: Any

    def to_dict(self) -> dict[str, Any]:
        answer: bool | int | None | str
        answer = self.answer

        evidence = self.evidence

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "answer": answer,
                "evidence": evidence,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_answer(data: object) -> bool | int | None | str:
            if data is None:
                return data
            return cast(bool | int | None | str, data)

        answer = _parse_answer(d.pop("answer"))

        evidence = d.pop("evidence")

        decision_question_example = cls(
            answer=answer,
            evidence=evidence,
        )

        return decision_question_example
