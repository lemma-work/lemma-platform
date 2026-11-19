from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.machine_file_entry import MachineFileEntry


T = TypeVar("T", bound="MachineFileListResponse")


@_attrs_define
class MachineFileListResponse:
    """
    Attributes:
        path (str):
        entries (list[MachineFileEntry] | Unset):
        exists (bool | Unset):  Default: True.
        home_root (str | Unset):  Default: '/home/user'.
        next_after (None | str | Unset):
        sleeping (bool | Unset): The machine is paused and was not woken; ask with `wake=true`. Default: False.
        truncated (bool | Unset):  Default: False.
        workspace_root (str | Unset):  Default: '/home/user/lemma'.
    """

    path: str
    entries: list[MachineFileEntry] | Unset = UNSET
    exists: bool | Unset = True
    home_root: str | Unset = "/home/user"
    next_after: None | str | Unset = UNSET
    sleeping: bool | Unset = False
    truncated: bool | Unset = False
    workspace_root: str | Unset = "/home/user/lemma"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        entries: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.entries, Unset):
            entries = []
            for entries_item_data in self.entries:
                entries_item = entries_item_data.to_dict()
                entries.append(entries_item)

        exists = self.exists

        home_root = self.home_root

        next_after: None | str | Unset
        if isinstance(self.next_after, Unset):
            next_after = UNSET
        else:
            next_after = self.next_after

        sleeping = self.sleeping

        truncated = self.truncated

        workspace_root = self.workspace_root

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "path": path,
            }
        )
        if entries is not UNSET:
            field_dict["entries"] = entries
        if exists is not UNSET:
            field_dict["exists"] = exists
        if home_root is not UNSET:
            field_dict["home_root"] = home_root
        if next_after is not UNSET:
            field_dict["next_after"] = next_after
        if sleeping is not UNSET:
            field_dict["sleeping"] = sleeping
        if truncated is not UNSET:
            field_dict["truncated"] = truncated
        if workspace_root is not UNSET:
            field_dict["workspace_root"] = workspace_root

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.machine_file_entry import MachineFileEntry

        d = dict(src_dict)
        path = d.pop("path")

        _entries = d.pop("entries", UNSET)
        entries: list[MachineFileEntry] | Unset = UNSET
        if _entries is not UNSET:
            entries = []
            for entries_item_data in _entries:
                entries_item = MachineFileEntry.from_dict(entries_item_data)

                entries.append(entries_item)

        exists = d.pop("exists", UNSET)

        home_root = d.pop("home_root", UNSET)

        def _parse_next_after(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_after = _parse_next_after(d.pop("next_after", UNSET))

        sleeping = d.pop("sleeping", UNSET)

        truncated = d.pop("truncated", UNSET)

        workspace_root = d.pop("workspace_root", UNSET)

        machine_file_list_response = cls(
            path=path,
            entries=entries,
            exists=exists,
            home_root=home_root,
            next_after=next_after,
            sleeping=sleeping,
            truncated=truncated,
            workspace_root=workspace_root,
        )

        machine_file_list_response.additional_properties = d
        return machine_file_list_response

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
