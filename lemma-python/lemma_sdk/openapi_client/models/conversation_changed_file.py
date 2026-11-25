from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.conversation_changed_file_status import ConversationChangedFileStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationChangedFile")


@_attrs_define
class ConversationChangedFile:
    """
    Attributes:
        additions (int):
        binary (bool): Git found no lines to count; additions and deletions are 0.
        deletions (int):
        path (str):
        status (ConversationChangedFileStatus):
        old_path (None | str | Unset): Where a renamed file was, before it moved.
    """

    additions: int
    binary: bool
    deletions: int
    path: str
    status: ConversationChangedFileStatus
    old_path: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        additions = self.additions

        binary = self.binary

        deletions = self.deletions

        path = self.path

        status = self.status.value

        old_path: None | str | Unset
        if isinstance(self.old_path, Unset):
            old_path = UNSET
        else:
            old_path = self.old_path

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "additions": additions,
                "binary": binary,
                "deletions": deletions,
                "path": path,
                "status": status,
            }
        )
        if old_path is not UNSET:
            field_dict["old_path"] = old_path

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        additions = d.pop("additions")

        binary = d.pop("binary")

        deletions = d.pop("deletions")

        path = d.pop("path")

        status = ConversationChangedFileStatus(d.pop("status"))

        def _parse_old_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        old_path = _parse_old_path(d.pop("old_path", UNSET))

        conversation_changed_file = cls(
            additions=additions,
            binary=binary,
            deletions=deletions,
            path=path,
            status=status,
            old_path=old_path,
        )

        conversation_changed_file.additional_properties = d
        return conversation_changed_file

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
