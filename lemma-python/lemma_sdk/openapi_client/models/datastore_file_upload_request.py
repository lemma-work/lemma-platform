from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, File, Unset

T = TypeVar("T", bound="DatastoreFileUploadRequest")


@_attrs_define
class DatastoreFileUploadRequest:
    """
    Attributes:
        data (File):
        description (None | str | Unset):
        directory_path (str | Unset):  Default: '/'.
        name (None | str | Unset):
        search_enabled (bool | Unset):  Default: True.
        visibility (None | str | Unset):
    """

    data: File
    description: None | str | Unset = UNSET
    directory_path: str | Unset = "/"
    name: None | str | Unset = UNSET
    search_enabled: bool | Unset = True
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.data.to_tuple()

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        directory_path = self.directory_path

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

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
                "data": data,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if directory_path is not UNSET:
            field_dict["directory_path"] = directory_path
        if name is not UNSET:
            field_dict["name"] = name
        if search_enabled is not UNSET:
            field_dict["search_enabled"] = search_enabled
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("data", self.data.to_tuple()))

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

        if not isinstance(self.directory_path, Unset):
            files.append(
                (
                    "directory_path",
                    (None, str(self.directory_path).encode(), "text/plain"),
                )
            )

        if not isinstance(self.name, Unset):
            if isinstance(self.name, str):
                files.append(("name", (None, str(self.name).encode(), "text/plain")))
            else:
                files.append(("name", (None, str(self.name).encode(), "text/plain")))

        if not isinstance(self.search_enabled, Unset):
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
        data = File(payload=BytesIO(d.pop("data")))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        directory_path = d.pop("directory_path", UNSET)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        search_enabled = d.pop("search_enabled", UNSET)

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        datastore_file_upload_request = cls(
            data=data,
            description=description,
            directory_path=directory_path,
            name=name,
            search_enabled=search_enabled,
            visibility=visibility,
        )

        datastore_file_upload_request.additional_properties = d
        return datastore_file_upload_request

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
