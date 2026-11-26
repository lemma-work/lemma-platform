from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GithubBotInstallation")


@_attrs_define
class GithubBotInstallation:
    """
    Attributes:
        login (None | str | Unset):
        repository_selection (None | str | Unset):
    """

    login: None | str | Unset = UNSET
    repository_selection: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        login: None | str | Unset
        if isinstance(self.login, Unset):
            login = UNSET
        else:
            login = self.login

        repository_selection: None | str | Unset
        if isinstance(self.repository_selection, Unset):
            repository_selection = UNSET
        else:
            repository_selection = self.repository_selection

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if login is not UNSET:
            field_dict["login"] = login
        if repository_selection is not UNSET:
            field_dict["repository_selection"] = repository_selection

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_login(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        login = _parse_login(d.pop("login", UNSET))

        def _parse_repository_selection(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repository_selection = _parse_repository_selection(
            d.pop("repository_selection", UNSET)
        )

        github_bot_installation = cls(
            login=login,
            repository_selection=repository_selection,
        )

        github_bot_installation.additional_properties = d
        return github_bot_installation

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
