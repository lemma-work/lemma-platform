from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.public_file_response import PublicFileResponse


T = TypeVar("T", bound="GroupPublicResponse")


@_attrs_define
class GroupPublicResponse:
    """The pod's Public files and tables, the first few of each.

    Attributes:
        files (list[PublicFileResponse]):
        more (bool): More is Public than is listed here.
        tables (list[str]):
    """

    files: list[PublicFileResponse]
    more: bool
    tables: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        more = self.more

        tables = self.tables

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "files": files,
                "more": more,
                "tables": tables,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.public_file_response import PublicFileResponse

        d = dict(src_dict)
        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = PublicFileResponse.from_dict(files_item_data)

            files.append(files_item)

        more = d.pop("more")

        tables = cast(list[str], d.pop("tables"))

        group_public_response = cls(
            files=files,
            more=more,
            tables=tables,
        )

        group_public_response.additional_properties = d
        return group_public_response

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
