from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.scorecard_unit_row import ScorecardUnitRow


T = TypeVar("T", bound="ScorecardRowsResponse")


@_attrs_define
class ScorecardRowsResponse:
    """
    Attributes:
        end (datetime.date):
        rows (list[ScorecardUnitRow]):
        start (datetime.date):
        truncated (bool | Unset): More rows fell in the week than are listed. Default: False.
    """

    end: datetime.date
    rows: list[ScorecardUnitRow]
    start: datetime.date
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        end = self.end.isoformat()

        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

        start = self.start.isoformat()

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "end": end,
                "rows": rows,
                "start": start,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.scorecard_unit_row import ScorecardUnitRow

        d = dict(src_dict)
        end = isoparse(d.pop("end")).date()

        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = ScorecardUnitRow.from_dict(rows_item_data)

            rows.append(rows_item)

        start = isoparse(d.pop("start")).date()

        truncated = d.pop("truncated", UNSET)

        scorecard_rows_response = cls(
            end=end,
            rows=rows,
            start=start,
            truncated=truncated,
        )

        scorecard_rows_response.additional_properties = d
        return scorecard_rows_response

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
