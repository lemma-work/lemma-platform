from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationRepositoryResponse")


@_attrs_define
class ConversationRepositoryResponse:
    """A GitHub repository a conversation can be opened in.

    Attributes:
        full_name (str): 'owner/name', the value create takes.
        name (str):
        owner (str):
        default_branch (None | str | Unset):
        private (bool | Unset):  Default: False.
    """

    full_name: str
    name: str
    owner: str
    default_branch: None | str | Unset = UNSET
    private: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        full_name = self.full_name

        name = self.name

        owner = self.owner

        default_branch: None | str | Unset
        if isinstance(self.default_branch, Unset):
            default_branch = UNSET
        else:
            default_branch = self.default_branch

        private = self.private

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "full_name": full_name,
                "name": name,
                "owner": owner,
            }
        )
        if default_branch is not UNSET:
            field_dict["default_branch"] = default_branch
        if private is not UNSET:
            field_dict["private"] = private

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        full_name = d.pop("full_name")

        name = d.pop("name")

        owner = d.pop("owner")

        def _parse_default_branch(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_branch = _parse_default_branch(d.pop("default_branch", UNSET))

        private = d.pop("private", UNSET)

        conversation_repository_response = cls(
            full_name=full_name,
            name=name,
            owner=owner,
            default_branch=default_branch,
            private=private,
        )

        conversation_repository_response.additional_properties = d
        return conversation_repository_response

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
