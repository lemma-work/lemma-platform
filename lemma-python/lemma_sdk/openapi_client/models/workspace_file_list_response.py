from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workspace_file_entry import WorkspaceFileEntry


T = TypeVar("T", bound="WorkspaceFileListResponse")


@_attrs_define
class WorkspaceFileListResponse:
    """
    Attributes:
        path (str): The directory that was listed.
        entries (list[WorkspaceFileEntry] | Unset):
        sleeping (bool | Unset): True when the workspace is paused and was not woken to answer. Entries are empty; ask
            again with `wake=true` to start it. Default: False.
        truncated (bool | Unset): True when the directory holds more entries than were returned. Default: False.
    """

    path: str
    entries: list[WorkspaceFileEntry] | Unset = UNSET
    sleeping: bool | Unset = False
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        entries: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.entries, Unset):
            entries = []
            for entries_item_data in self.entries:
                entries_item = entries_item_data.to_dict()
                entries.append(entries_item)

        sleeping = self.sleeping

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "path": path,
            }
        )
        if entries is not UNSET:
            field_dict["entries"] = entries
        if sleeping is not UNSET:
            field_dict["sleeping"] = sleeping
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.workspace_file_entry import WorkspaceFileEntry

        d = dict(src_dict)
        path = d.pop("path")

        _entries = d.pop("entries", UNSET)
        entries: list[WorkspaceFileEntry] | Unset = UNSET
        if _entries is not UNSET:
            entries = []
            for entries_item_data in _entries:
                entries_item = WorkspaceFileEntry.from_dict(entries_item_data)

                entries.append(entries_item)

        sleeping = d.pop("sleeping", UNSET)

        truncated = d.pop("truncated", UNSET)

        workspace_file_list_response = cls(
            path=path,
            entries=entries,
            sleeping=sleeping,
            truncated=truncated,
        )

        workspace_file_list_response.additional_properties = d
        return workspace_file_list_response

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
