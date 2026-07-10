from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, File, FileTypes, Unset

T = TypeVar("T", bound="Update")


@_attrs_define
class Update:
    """
    Attributes:
        path (str):
        data (File | None | Unset):
        description (None | str | Unset):
        new_path (None | str | Unset):
        search_enabled (bool | None | Unset):
        visibility (None | str | Unset):
    """

    path: str
    data: File | None | Unset = UNSET
    description: None | str | Unset = UNSET
    new_path: None | str | Unset = UNSET
    search_enabled: bool | None | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        data: FileTypes | None | Unset
        if isinstance(self.data, Unset):
            data = UNSET
        elif isinstance(self.data, File):
            data = self.data.to_tuple()

        else:
            data = self.data

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        new_path: None | str | Unset
        if isinstance(self.new_path, Unset):
            new_path = UNSET
        else:
            new_path = self.new_path

        search_enabled: bool | None | Unset
        if isinstance(self.search_enabled, Unset):
            search_enabled = UNSET
        else:
            search_enabled = self.search_enabled

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "path": path,
            }
        )
        if data is not UNSET:
            field_dict["data"] = data
        if description is not UNSET:
            field_dict["description"] = description
        if new_path is not UNSET:
            field_dict["new_path"] = new_path
        if search_enabled is not UNSET:
            field_dict["search_enabled"] = search_enabled
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("path", (None, str(self.path).encode(), "text/plain")))

        if not isinstance(self.data, Unset):
            if isinstance(self.data, File):
                files.append(("data", self.data.to_tuple()))
            else:
                files.append(("data", (None, str(self.data).encode(), "text/plain")))

        if not isinstance(self.description, Unset):
            if isinstance(self.description, str):
                files.append(
                    (
                        "description",
                        (None, str(self.description).encode(), "text/plain"),
                    )
                )
            else:
                files.append(
                    (
                        "description",
                        (None, str(self.description).encode(), "text/plain"),
                    )
                )

        if not isinstance(self.new_path, Unset):
            if isinstance(self.new_path, str):
                files.append(
                    ("new_path", (None, str(self.new_path).encode(), "text/plain"))
                )
            else:
                files.append(
                    ("new_path", (None, str(self.new_path).encode(), "text/plain"))
                )

        if not isinstance(self.search_enabled, Unset):
            if isinstance(self.search_enabled, bool):
                files.append(
                    (
                        "search_enabled",
                        (None, str(self.search_enabled).encode(), "text/plain"),
                    )
                )
            else:
                files.append(
                    (
                        "search_enabled",
                        (None, str(self.search_enabled).encode(), "text/plain"),
                    )
                )

        if not isinstance(self.visibility, Unset):
            if isinstance(self.visibility, str):
                files.append(
                    ("visibility", (None, str(self.visibility).encode(), "text/plain"))
                )
            else:
                files.append(
                    ("visibility", (None, str(self.visibility).encode(), "text/plain"))
                )

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        path = d.pop("path")

        def _parse_data(data: object) -> File | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, bytes):
                    raise TypeError()
                data_type_0 = File(payload=BytesIO(data))

                return data_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(File | None | Unset, data)

        data = _parse_data(d.pop("data", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_new_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        new_path = _parse_new_path(d.pop("new_path", UNSET))

        def _parse_search_enabled(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        search_enabled = _parse_search_enabled(d.pop("search_enabled", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update = cls(
            path=path,
            data=data,
            description=description,
            new_path=new_path,
            search_enabled=search_enabled,
            visibility=visibility,
        )

        update.additional_properties = d
        return update

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
