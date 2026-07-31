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
        data_tables (list[str] | None | Unset): Opt-in per-table seed selection: include row data only for these named
            tables (the common case — ship a few setup/config tables' rows). Ignored for names that aren't real tables (a
            warning is surfaced). Combined with `with_data` as a union; omit both for a resources-only export.
        include (list[str] | None | Unset): Optional list of resource types to include (e.g. ['tables', 'agents']). Omit
            to export every supported resource type.
        ttl_seconds (int | None | Unset): Requested lifetime (seconds) of the signed download URL + archive retention.
            Clamped to the configured maximum; omit for the default.
        with_data (bool | Unset): Opt-in: include row data for EVERY table (data.csv per table) as seed/default data.
            Off by default — an export carries only pod resources, which recreate the pod in an empty-table state. Prefer
            `data_tables` to seed only specific setup tables; enable this only to seed the whole pod. Row data is capped
            (per-table + total) regardless. Default: False.
        with_files (bool | Unset): Opt-in: include the pod's POD-visible file storage (folders + file bytes) in the
            bundle. Off by default. File bytes share a conservative size budget with table row data (meant for small
            skill/script/seed files, not a bulk file dump). Default: False.
    """

    data_tables: list[str] | None | Unset = UNSET
    include: list[str] | None | Unset = UNSET
    ttl_seconds: int | None | Unset = UNSET
    with_data: bool | Unset = False
    with_files: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data_tables: list[str] | None | Unset
        if isinstance(self.data_tables, Unset):
            data_tables = UNSET
        elif isinstance(self.data_tables, list):
            data_tables = self.data_tables

        else:
            data_tables = self.data_tables

        include: list[str] | None | Unset
        if isinstance(self.include, Unset):
            include = UNSET
        elif isinstance(self.include, list):
            include = self.include

        else:
            include = self.include

        ttl_seconds: int | None | Unset
        if isinstance(self.ttl_seconds, Unset):
            ttl_seconds = UNSET
        else:
            ttl_seconds = self.ttl_seconds

        with_data = self.with_data

        with_files = self.with_files

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if data_tables is not UNSET:
            field_dict["data_tables"] = data_tables
        if include is not UNSET:
            field_dict["include"] = include
        if ttl_seconds is not UNSET:
            field_dict["ttl_seconds"] = ttl_seconds
        if with_data is not UNSET:
            field_dict["with_data"] = with_data
        if with_files is not UNSET:
            field_dict["with_files"] = with_files

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_data_tables(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                data_tables_type_0 = cast(list[str], data)

                return data_tables_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        data_tables = _parse_data_tables(d.pop("data_tables", UNSET))

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
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        include = _parse_include(d.pop("include", UNSET))

        def _parse_ttl_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        ttl_seconds = _parse_ttl_seconds(d.pop("ttl_seconds", UNSET))

        with_data = d.pop("with_data", UNSET)

        with_files = d.pop("with_files", UNSET)

        export_start_request = cls(
            data_tables=data_tables,
            include=include,
            ttl_seconds=ttl_seconds,
            with_data=with_data,
            with_files=with_files,
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
