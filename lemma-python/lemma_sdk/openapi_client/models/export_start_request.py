from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ExportStartRequest")


@_attrs_define
class ExportStartRequest:
    """Body for starting a pod export.

    Attributes:
        data_tables (list[str] | None | Unset): Tables whose rows to seed into the bundle, named one by one. There is
            deliberately no 'every table' switch: row data is the part of a pod most likely to be private, so it leaves the
            pod only for tables the caller actually asked for. Omit for a bundle with no row data. A name that is not a
            table in this pod is skipped with a warning. Row data is capped (per-table and in total) regardless.
        file_folders (list[str] | None | Unset): Folder paths whose contents to include, named one by one (e.g.
            ['/reports', '/config']). Each is exported with everything beneath it. As with `data_tables` there is no 'every
            folder' switch. Omit for a bundle with no files. A path that is not a folder in this pod is skipped with a
            warning. File bytes share a conservative size budget with table row data.
        include (list[str] | None | Unset): Optional list of resource types to include (e.g. ['tables', 'agents']). Omit
            to export every supported resource type.
        ttl_seconds (int | None | Unset): Requested lifetime (seconds) of the signed download URL + archive retention.
            Clamped to the configured maximum; omit for the default.
    """

    data_tables: list[str] | None | Unset = UNSET
    file_folders: list[str] | None | Unset = UNSET
    include: list[str] | None | Unset = UNSET
    ttl_seconds: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        data_tables: list[str] | None | Unset
        if isinstance(self.data_tables, Unset):
            data_tables = UNSET
        elif isinstance(self.data_tables, list):
            data_tables = self.data_tables

        else:
            data_tables = self.data_tables

        file_folders: list[str] | None | Unset
        if isinstance(self.file_folders, Unset):
            file_folders = UNSET
        elif isinstance(self.file_folders, list):
            file_folders = self.file_folders

        else:
            file_folders = self.file_folders

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

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if data_tables is not UNSET:
            field_dict["data_tables"] = data_tables
        if file_folders is not UNSET:
            field_dict["file_folders"] = file_folders
        if include is not UNSET:
            field_dict["include"] = include
        if ttl_seconds is not UNSET:
            field_dict["ttl_seconds"] = ttl_seconds

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

        def _parse_file_folders(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                file_folders_type_0 = cast(list[str], data)

                return file_folders_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        file_folders = _parse_file_folders(d.pop("file_folders", UNSET))

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

        export_start_request = cls(
            data_tables=data_tables,
            file_folders=file_folders,
            include=include,
            ttl_seconds=ttl_seconds,
        )

        return export_start_request
