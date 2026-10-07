from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.scorecard_measure_history import ScorecardMeasureHistory
    from ..models.scorecard_window import ScorecardWindow


T = TypeVar("T", bound="ScorecardPreviewResponse")


@_attrs_define
class ScorecardPreviewResponse:
    """
    Attributes:
        measures (list[ScorecardMeasureHistory]):
        windows (list[ScorecardWindow]):
        measures_skipped (int | Unset): Measures chosen but not counted by this preview. Default: 0.
    """

    measures: list[ScorecardMeasureHistory]
    windows: list[ScorecardWindow]
    measures_skipped: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        measures = []
        for measures_item_data in self.measures:
            measures_item = measures_item_data.to_dict()
            measures.append(measures_item)

        windows = []
        for windows_item_data in self.windows:
            windows_item = windows_item_data.to_dict()
            windows.append(windows_item)

        measures_skipped = self.measures_skipped

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "measures": measures,
                "windows": windows,
            }
        )
        if measures_skipped is not UNSET:
            field_dict["measures_skipped"] = measures_skipped

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.scorecard_measure_history import ScorecardMeasureHistory
        from ..models.scorecard_window import ScorecardWindow

        d = dict(src_dict)
        measures = []
        _measures = d.pop("measures")
        for measures_item_data in _measures:
            measures_item = ScorecardMeasureHistory.from_dict(measures_item_data)

            measures.append(measures_item)

        windows = []
        _windows = d.pop("windows")
        for windows_item_data in _windows:
            windows_item = ScorecardWindow.from_dict(windows_item_data)

            windows.append(windows_item)

        measures_skipped = d.pop("measures_skipped", UNSET)

        scorecard_preview_response = cls(
            measures=measures,
            windows=windows,
            measures_skipped=measures_skipped,
        )

        scorecard_preview_response.additional_properties = d
        return scorecard_preview_response

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
