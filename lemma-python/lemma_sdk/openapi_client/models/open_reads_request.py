from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.public_audience import PublicAudience
from ..types import UNSET, Unset

T = TypeVar("T", bound="OpenReadsRequest")


@_attrs_define
class OpenReadsRequest:
    """
    Attributes:
        audience (PublicAudience): Who outside the pod may add rows.
        columns (list[str]): The columns people outside may see, in the order to show them. Checked against the table
            when it is opened.
        order_by (None | str | Unset): One of the open columns, to read rows in ascending order of.
    """

    audience: PublicAudience
    columns: list[str]
    order_by: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        audience = self.audience.value

        columns = self.columns

        order_by: None | str | Unset
        if isinstance(self.order_by, Unset):
            order_by = UNSET
        else:
            order_by = self.order_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "audience": audience,
                "columns": columns,
            }
        )
        if order_by is not UNSET:
            field_dict["order_by"] = order_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        audience = PublicAudience(d.pop("audience"))

        columns = cast(list[str], d.pop("columns"))

        def _parse_order_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        order_by = _parse_order_by(d.pop("order_by", UNSET))

        open_reads_request = cls(
            audience=audience,
            columns=columns,
            order_by=order_by,
        )

        open_reads_request.additional_properties = d
        return open_reads_request

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
