from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.decider_test_response_agreement import DeciderTestResponseAgreement
    from ..models.decider_test_response_answers_item import (
        DeciderTestResponseAnswersItem,
    )
    from ..models.disagreement_response import DisagreementResponse


T = TypeVar("T", bound="DeciderTestResponse")


@_attrs_define
class DeciderTestResponse:
    """
    Attributes:
        agreement (DeciderTestResponseAgreement):
        answers (list[DeciderTestResponseAnswersItem]):
        disagreements (list[DisagreementResponse]):
        open_ (list[list[str]]):
    """

    agreement: DeciderTestResponseAgreement
    answers: list[DeciderTestResponseAnswersItem]
    disagreements: list[DisagreementResponse]
    open_: list[list[str]]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agreement = self.agreement.to_dict()

        answers = []
        for answers_item_data in self.answers:
            answers_item = answers_item_data.to_dict()
            answers.append(answers_item)

        disagreements = []
        for disagreements_item_data in self.disagreements:
            disagreements_item = disagreements_item_data.to_dict()
            disagreements.append(disagreements_item)

        open_ = []
        for open_item_data in self.open_:
            open_item = open_item_data

            open_.append(open_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agreement": agreement,
                "answers": answers,
                "disagreements": disagreements,
                "open": open_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_test_response_agreement import (
            DeciderTestResponseAgreement,
        )
        from ..models.decider_test_response_answers_item import (
            DeciderTestResponseAnswersItem,
        )
        from ..models.disagreement_response import DisagreementResponse

        d = dict(src_dict)
        agreement = DeciderTestResponseAgreement.from_dict(d.pop("agreement"))

        answers = []
        _answers = d.pop("answers")
        for answers_item_data in _answers:
            answers_item = DeciderTestResponseAnswersItem.from_dict(answers_item_data)

            answers.append(answers_item)

        disagreements = []
        _disagreements = d.pop("disagreements")
        for disagreements_item_data in _disagreements:
            disagreements_item = DisagreementResponse.from_dict(disagreements_item_data)

            disagreements.append(disagreements_item)

        open_ = []
        _open_ = d.pop("open")
        for open_item_data in _open_:
            open_item = cast(list[str], open_item_data)

            open_.append(open_item)

        decider_test_response = cls(
            agreement=agreement,
            answers=answers,
            disagreements=disagreements,
            open_=open_,
        )

        decider_test_response.additional_properties = d
        return decider_test_response

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
