from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.decision_example_body_answers import DecisionExampleBodyAnswers


T = TypeVar("T", bound="DecisionExampleBody")


@_attrs_define
class DecisionExampleBody:
    """
    Attributes:
        answers (DecisionExampleBodyAnswers): How that case was answered, by question key. Questions may be left out;
            null means the right answer there was 'can't tell'.
        evidence (Any):
    """

    answers: DecisionExampleBodyAnswers
    evidence: Any

    def to_dict(self) -> dict[str, Any]:
        answers = self.answers.to_dict()

        evidence = self.evidence

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "answers": answers,
                "evidence": evidence,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_example_body_answers import DecisionExampleBodyAnswers

        d = dict(src_dict)
        answers = DecisionExampleBodyAnswers.from_dict(d.pop("answers"))

        evidence = d.pop("evidence")

        decision_example_body = cls(
            answers=answers,
            evidence=evidence,
        )

        return decision_example_body
