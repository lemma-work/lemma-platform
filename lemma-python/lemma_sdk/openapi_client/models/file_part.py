from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="FilePart")


@_attrs_define
class FilePart:
    """A file of the pod's the line shares.

    Whoever writes the line must be able to read the file; it is then read
    through the line (``GET …/messages/{m}/files/{file_id}``), by whoever may
    see the line, as its writer reads it -- never through the file's own
    folder, which a private channel's readers need not share.

        Attributes:
            name (str):
            path (str):
            type_ (Literal['file']):
            file_id (None | Unset | UUID):
            mime_type (None | str | Unset):
            size (int | None | Unset):
    """

    name: str
    path: str
    type_: Literal["file"]
    file_id: None | Unset | UUID = UNSET
    mime_type: None | str | Unset = UNSET
    size: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        path = self.path

        type_ = self.type_

        file_id: None | str | Unset
        if isinstance(self.file_id, Unset):
            file_id = UNSET
        elif isinstance(self.file_id, UUID):
            file_id = str(self.file_id)
        else:
            file_id = self.file_id

        mime_type: None | str | Unset
        if isinstance(self.mime_type, Unset):
            mime_type = UNSET
        else:
            mime_type = self.mime_type

        size: int | None | Unset
        if isinstance(self.size, Unset):
            size = UNSET
        else:
            size = self.size

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "path": path,
                "type": type_,
            }
        )
        if file_id is not UNSET:
            field_dict["file_id"] = file_id
        if mime_type is not UNSET:
            field_dict["mime_type"] = mime_type
        if size is not UNSET:
            field_dict["size"] = size

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        path = d.pop("path")

        type_ = cast(Literal["file"], d.pop("type"))
        if type_ != "file":
            raise ValueError(f"type must match const 'file', got '{type_}'")

        def _parse_file_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                file_id_type_0 = UUID(data)

                return file_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        file_id = _parse_file_id(d.pop("file_id", UNSET))

        def _parse_mime_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mime_type = _parse_mime_type(d.pop("mime_type", UNSET))

        def _parse_size(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        size = _parse_size(d.pop("size", UNSET))

        file_part = cls(
            name=name,
            path=path,
            type_=type_,
            file_id=file_id,
            mime_type=mime_type,
            size=size,
        )

        file_part.additional_properties = d
        return file_part

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
