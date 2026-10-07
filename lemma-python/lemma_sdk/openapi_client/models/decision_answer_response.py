from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="DecisionAnswerResponse")


@_attrs_define
class DecisionAnswerResponse:
    """
    Attributes:
        confidence (float | None): The provider's probability for this value, when it measures one. Null from providers
            that do not, such as a language model.
        value (bool | int | list[str] | None | str): The answer, or null when the evidence did not support one. Null is
            an answer, not a failure: a failure is an error response.
    """

    confidence: float | None
    value: bool | int | list[str] | None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        confidence: float | None
        confidence = self.confidence

        value: bool | int | list[str] | None | str
        if isinstance(self.value, list):
            value = self.value

        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "confidence": confidence,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_confidence(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        confidence = _parse_confidence(d.pop("confidence"))

        def _parse_value(data: object) -> bool | int | list[str] | None | str:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                value_type_3 = cast(list[str], data)

                return value_type_3
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(bool | int | list[str] | None | str, data)

        value = _parse_value(d.pop("value"))

        decision_answer_response = cls(
            confidence=confidence,
            value=value,
        )

        decision_answer_response.additional_properties = d
        return decision_answer_response

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
