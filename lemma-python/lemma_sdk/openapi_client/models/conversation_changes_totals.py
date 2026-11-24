from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationChangesTotals")


@_attrs_define
class ConversationChangesTotals:
    """
    Attributes:
        additions (int | Unset):  Default: 0.
        deletions (int | Unset):  Default: 0.
        files (int | Unset):  Default: 0.
    """

    additions: int | Unset = 0
    deletions: int | Unset = 0
    files: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        additions = self.additions

        deletions = self.deletions

        files = self.files

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if additions is not UNSET:
            field_dict["additions"] = additions
        if deletions is not UNSET:
            field_dict["deletions"] = deletions
        if files is not UNSET:
            field_dict["files"] = files

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        additions = d.pop("additions", UNSET)

        deletions = d.pop("deletions", UNSET)

        files = d.pop("files", UNSET)

        conversation_changes_totals = cls(
            additions=additions,
            deletions=deletions,
            files=files,
        )

        conversation_changes_totals.additional_properties = d
        return conversation_changes_totals

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
