from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GithubInstallationOffer")


@_attrs_define
class GithubInstallationOffer:
    """
    Attributes:
        installation_id (str):
        login (None | str | Unset):
        manage_url (None | str | Unset): The installation's settings page on GitHub: where its repositories, and a
            pending permission, are changed.
    """

    installation_id: str
    login: None | str | Unset = UNSET
    manage_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        installation_id = self.installation_id

        login: None | str | Unset
        if isinstance(self.login, Unset):
            login = UNSET
        else:
            login = self.login

        manage_url: None | str | Unset
        if isinstance(self.manage_url, Unset):
            manage_url = UNSET
        else:
            manage_url = self.manage_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "installation_id": installation_id,
            }
        )
        if login is not UNSET:
            field_dict["login"] = login
        if manage_url is not UNSET:
            field_dict["manage_url"] = manage_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        installation_id = d.pop("installation_id")

        def _parse_login(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        login = _parse_login(d.pop("login", UNSET))

        def _parse_manage_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        manage_url = _parse_manage_url(d.pop("manage_url", UNSET))

        github_installation_offer = cls(
            installation_id=installation_id,
            login=login,
            manage_url=manage_url,
        )

        github_installation_offer.additional_properties = d
        return github_installation_offer

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
