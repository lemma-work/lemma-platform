from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.scorecard_week_score import ScorecardWeekScore


T = TypeVar("T", bound="ScorecardMeasureHistory")


@_attrs_define
class ScorecardMeasureHistory:
    """
    Attributes:
        key (str):
        measure (str):
        weeks (list[ScorecardWeekScore]): One per window, oldest first.
    """

    key: str
    measure: str
    weeks: list[ScorecardWeekScore]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        measure = self.measure

        weeks = []
        for weeks_item_data in self.weeks:
            weeks_item = weeks_item_data.to_dict()
            weeks.append(weeks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "measure": measure,
                "weeks": weeks,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.scorecard_week_score import ScorecardWeekScore

        d = dict(src_dict)
        key = d.pop("key")

        measure = d.pop("measure")

        weeks = []
        _weeks = d.pop("weeks")
        for weeks_item_data in _weeks:
            weeks_item = ScorecardWeekScore.from_dict(weeks_item_data)

            weeks.append(weeks_item)

        scorecard_measure_history = cls(
            key=key,
            measure=measure,
            weeks=weeks,
        )

        scorecard_measure_history.additional_properties = d
        return scorecard_measure_history

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
