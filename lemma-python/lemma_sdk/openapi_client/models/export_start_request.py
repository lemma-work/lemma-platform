from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ExportStartRequest")


@_attrs_define
class ExportStartRequest:
    """Body for starting a pod export.

    Attributes:
        include (list[str] | None | Unset): Optional list of resource types to include (e.g. ['tables', 'agents']). Omit
            to export every supported resource type.
        with_data (bool | Unset): Include table row data (data.csv per table) in the bundle. Default: True.
    """

    include: list[str] | None | Unset = UNSET
    with_data: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        include: list[str] | None | Unset
        if isinstance(self.include, Unset):
            include = UNSET
        elif isinstance(self.include, list):
            include = self.include

        else:
            include = self.include

        with_data = self.with_data

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if include is not UNSET:
            field_dict["include"] = include
        if with_data is not UNSET:
            field_dict["with_data"] = with_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_include(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                include_type_0 = cast(list[str], data)

                return include_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        include = _parse_include(d.pop("include", UNSET))

        with_data = d.pop("with_data", UNSET)

        export_start_request = cls(
            include=include,
            with_data=with_data,
        )

        export_start_request.additional_properties = d
        return export_start_request

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
