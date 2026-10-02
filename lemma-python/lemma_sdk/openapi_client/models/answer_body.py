from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.answer_body_by import AnswerBodyBy
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.answer_body_answers import AnswerBodyAnswers


T = TypeVar("T", bound="AnswerBody")


@_attrs_define
class AnswerBody:
    """
    Attributes:
        answers (AnswerBodyAnswers):
        by (AnswerBodyBy | Unset): Who answered. A person's answer teaches the decider; an agent's is recorded and never
            becomes an example. Default: AnswerBodyBy.PERSON.
    """

    answers: AnswerBodyAnswers
    by: AnswerBodyBy | Unset = AnswerBodyBy.PERSON

    def to_dict(self) -> dict[str, Any]:
        answers = self.answers.to_dict()

        by: str | Unset = UNSET
        if not isinstance(self.by, Unset):
            by = self.by.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "answers": answers,
            }
        )
        if by is not UNSET:
            field_dict["by"] = by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer_body_answers import AnswerBodyAnswers

        d = dict(src_dict)
        answers = AnswerBodyAnswers.from_dict(d.pop("answers"))

        _by = d.pop("by", UNSET)
        by: AnswerBodyBy | Unset
        if isinstance(_by, Unset):
            by = UNSET
        else:
            by = AnswerBodyBy(_by)

        answer_body = cls(
            answers=answers,
            by=by,
        )

        return answer_body
