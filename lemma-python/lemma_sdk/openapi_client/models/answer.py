from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.rung import Rung
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.answer_distribution_type_0 import AnswerDistributionType0


T = TypeVar("T", bound="Answer")


@_attrs_define
class Answer:
    """One question's answer and where it came from.

    `distribution` and `confidence` are System One's and exist only when it
    answered. A model's answer carries neither, and nothing here invents one.
    `abstained` marks a fallback taken because no rung committed.

        Attributes:
            by (Rung): Who answered. The first three are the ladder; the last two resolve it.
            value (bool | int | list[str] | str):
            abstained (bool | Unset):  Default: False.
            confidence (float | None | Unset):
            distribution (AnswerDistributionType0 | None | Unset):
    """

    by: Rung
    value: bool | int | list[str] | str
    abstained: bool | Unset = False
    confidence: float | None | Unset = UNSET
    distribution: AnswerDistributionType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.answer_distribution_type_0 import AnswerDistributionType0

        by = self.by.value

        value: bool | int | list[str] | str
        if isinstance(self.value, list):
            value = self.value

        else:
            value = self.value

        abstained = self.abstained

        confidence: float | None | Unset
        if isinstance(self.confidence, Unset):
            confidence = UNSET
        else:
            confidence = self.confidence

        distribution: dict[str, Any] | None | Unset
        if isinstance(self.distribution, Unset):
            distribution = UNSET
        elif isinstance(self.distribution, AnswerDistributionType0):
            distribution = self.distribution.to_dict()
        else:
            distribution = self.distribution

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "by": by,
                "value": value,
            }
        )
        if abstained is not UNSET:
            field_dict["abstained"] = abstained
        if confidence is not UNSET:
            field_dict["confidence"] = confidence
        if distribution is not UNSET:
            field_dict["distribution"] = distribution

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer_distribution_type_0 import AnswerDistributionType0

        d = dict(src_dict)
        by = Rung(d.pop("by"))

        def _parse_value(data: object) -> bool | int | list[str] | str:
            try:
                if not isinstance(data, list):
                    raise TypeError()
                componentsschemas_answer_value_type_1 = cast(list[str], data)

                return componentsschemas_answer_value_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(bool | int | list[str] | str, data)

        value = _parse_value(d.pop("value"))

        abstained = d.pop("abstained", UNSET)

        def _parse_confidence(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        confidence = _parse_confidence(d.pop("confidence", UNSET))

        def _parse_distribution(data: object) -> AnswerDistributionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                distribution_type_0 = AnswerDistributionType0.from_dict(data)

                return distribution_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AnswerDistributionType0 | None | Unset, data)

        distribution = _parse_distribution(d.pop("distribution", UNSET))

        answer = cls(
            by=by,
            value=value,
            abstained=abstained,
            confidence=confidence,
            distribution=distribution,
        )

        answer.additional_properties = d
        return answer

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
