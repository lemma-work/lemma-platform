from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.decision_response_answers import DecisionResponseAnswers
    from ..models.decision_usage_response import DecisionUsageResponse


T = TypeVar("T", bound="DecisionResponse")


@_attrs_define
class DecisionResponse:
    """
    Attributes:
        answers (DecisionResponseAnswers): One answer per question, by key.
        model (None | str): The model that ran.
        provider (str): The provider that answered.
        usage (DecisionUsageResponse):
    """

    answers: DecisionResponseAnswers
    model: None | str
    provider: str
    usage: DecisionUsageResponse
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answers = self.answers.to_dict()

        model: None | str
        model = self.model

        provider = self.provider

        usage = self.usage.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answers": answers,
                "model": model,
                "provider": provider,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_response_answers import DecisionResponseAnswers
        from ..models.decision_usage_response import DecisionUsageResponse

        d = dict(src_dict)
        answers = DecisionResponseAnswers.from_dict(d.pop("answers"))

        def _parse_model(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        model = _parse_model(d.pop("model"))

        provider = d.pop("provider")

        usage = DecisionUsageResponse.from_dict(d.pop("usage"))

        decision_response = cls(
            answers=answers,
            model=model,
            provider=provider,
            usage=usage,
        )

        decision_response.additional_properties = d
        return decision_response

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
