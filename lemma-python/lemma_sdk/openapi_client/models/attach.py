from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, File, FileTypes, Unset

T = TypeVar("T", bound="Attach")


@_attrs_define
class Attach:
    """
    Attributes:
        data (File):
        path (str):
        images (list[File] | Unset):
    """

    data: File
    path: str
    images: list[File] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.data.to_tuple()

        path = self.path

        images: list[FileTypes] | Unset = UNSET
        if not isinstance(self.images, Unset):
            images = []
            for images_item_data in self.images:
                images_item = images_item_data.to_tuple()

                images.append(images_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data": data,
                "path": path,
            }
        )
        if images is not UNSET:
            field_dict["images"] = images

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("data", self.data.to_tuple()))

        files.append(("path", (None, str(self.path).encode(), "text/plain")))

        if not isinstance(self.images, Unset):
            for images_item_element in self.images:
                files.append(("images", images_item_element.to_tuple()))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        data = File(payload=BytesIO(d.pop("data")))

        path = d.pop("path")

        _images = d.pop("images", UNSET)
        images: list[File] | Unset = UNSET
        if _images is not UNSET:
            images = []
            for images_item_data in _images:
                images_item = File(payload=BytesIO(images_item_data))

                images.append(images_item)

        attach = cls(
            data=data,
            path=path,
            images=images,
        )

        attach.additional_properties = d
        return attach

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
