from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.row_result_response import RowResultResponse
    from ..models.rows_response_counts import RowsResponseCounts


T = TypeVar("T", bound="RowsResponse")


@_attrs_define
class RowsResponse:
    """
    Attributes:
        counts (RowsResponseCounts): Per question, how many rows got each answer.
        decider_key (str):
        failed (int):
        rows (list[RowResultResponse]):
    """

    counts: RowsResponseCounts
    decider_key: str
    failed: int
    rows: list[RowResultResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts.to_dict()

        decider_key = self.decider_key

        failed = self.failed

        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "counts": counts,
                "decider_key": decider_key,
                "failed": failed,
                "rows": rows,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.row_result_response import RowResultResponse
        from ..models.rows_response_counts import RowsResponseCounts

        d = dict(src_dict)
        counts = RowsResponseCounts.from_dict(d.pop("counts"))

        decider_key = d.pop("decider_key")

        failed = d.pop("failed")

        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = RowResultResponse.from_dict(rows_item_data)

            rows.append(rows_item)

        rows_response = cls(
            counts=counts,
            decider_key=decider_key,
            failed=failed,
            rows=rows,
        )

        rows_response.additional_properties = d
        return rows_response

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
