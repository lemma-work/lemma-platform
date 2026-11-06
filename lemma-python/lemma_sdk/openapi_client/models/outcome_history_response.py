from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.outcome_week_response import OutcomeWeekResponse


T = TypeVar("T", bound="OutcomeHistoryResponse")


@_attrs_define
class OutcomeHistoryResponse:
    """
    Attributes:
        weeks (list[OutcomeWeekResponse]): Oldest first, ending with this week. Only the weeks read by the pod's current
            measure: changing the measure starts a new history.
    """

    weeks: list[OutcomeWeekResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        weeks = []
        for weeks_item_data in self.weeks:
            weeks_item = weeks_item_data.to_dict()
            weeks.append(weeks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "weeks": weeks,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.outcome_week_response import OutcomeWeekResponse

        d = dict(src_dict)
        weeks = []
        _weeks = d.pop("weeks")
        for weeks_item_data in _weeks:
            weeks_item = OutcomeWeekResponse.from_dict(weeks_item_data)

            weeks.append(weeks_item)

        outcome_history_response = cls(
            weeks=weeks,
        )

        outcome_history_response.additional_properties = d
        return outcome_history_response

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
