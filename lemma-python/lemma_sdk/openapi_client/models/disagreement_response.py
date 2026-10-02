from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.answer import Answer


T = TypeVar("T", bound="DisagreementResponse")


@_attrs_define
class DisagreementResponse:
    """
    Attributes:
        answer (Answer | None):
        expected (bool | int | list[str] | str):
        question (str):
        row (int):
    """

    answer: Answer | None
    expected: bool | int | list[str] | str
    question: str
    row: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.answer import Answer

        answer: dict[str, Any] | None
        if isinstance(self.answer, Answer):
            answer = self.answer.to_dict()
        else:
            answer = self.answer

        expected: bool | int | list[str] | str
        if isinstance(self.expected, list):
            expected = self.expected

        else:
            expected = self.expected

        question = self.question

        row = self.row

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answer": answer,
                "expected": expected,
                "question": question,
                "row": row,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer import Answer

        d = dict(src_dict)

        def _parse_answer(data: object) -> Answer | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                answer_type_0 = Answer.from_dict(data)

                return answer_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(Answer | None, data)

        answer = _parse_answer(d.pop("answer"))

        def _parse_expected(data: object) -> bool | int | list[str] | str:
            try:
                if not isinstance(data, list):
                    raise TypeError()
                componentsschemas_answer_value_type_1 = cast(list[str], data)

                return componentsschemas_answer_value_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(bool | int | list[str] | str, data)

        expected = _parse_expected(d.pop("expected"))

        question = d.pop("question")

        row = d.pop("row")

        disagreement_response = cls(
            answer=answer,
            expected=expected,
            question=question,
            row=row,
        )

        disagreement_response.additional_properties = d
        return disagreement_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
