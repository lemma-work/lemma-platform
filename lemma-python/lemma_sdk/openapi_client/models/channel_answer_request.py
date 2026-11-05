from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.channel_answer_request_answer import ChannelAnswerRequestAnswer


T = TypeVar("T", bound="ChannelAnswerRequest")


@_attrs_define
class ChannelAnswerRequest:
    """
    Attributes:
        answer (ChannelAnswerRequestAnswer): The answer, in the question's terms: `{choice}` or `{text}` for a question,
            `{decision, note?}` for an approval, the form's fields for a form.
    """

    answer: ChannelAnswerRequestAnswer
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answer = self.answer.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answer": answer,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_answer_request_answer import ChannelAnswerRequestAnswer

        d = dict(src_dict)
        answer = ChannelAnswerRequestAnswer.from_dict(d.pop("answer"))

        channel_answer_request = cls(
            answer=answer,
        )

        channel_answer_request.additional_properties = d
        return channel_answer_request

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
