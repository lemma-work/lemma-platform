from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.github_wait import GithubWait
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.github_installation_offer import GithubInstallationOffer


T = TypeVar("T", bound="ConnectionGithub")


@_attrs_define
class ConnectionGithub:
    """With ``install``: why GitHub is not usable here yet, and the way on.

    Attributes:
        wait (GithubWait): Why a GitHub account here reaches no installation, by who can fix it.
        choices (list[GithubInstallationOffer] | Unset): With `choose`: the installations the account can see; otherwise
            the one in question.
        install_url (None | str | Unset): With `install`: where the App is installed.
        login (None | str | Unset): The GitHub account the installation in question is on.
        manage_url (None | str | Unset):
    """

    wait: GithubWait
    choices: list[GithubInstallationOffer] | Unset = UNSET
    install_url: None | str | Unset = UNSET
    login: None | str | Unset = UNSET
    manage_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        wait = self.wait.value

        choices: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.choices, Unset):
            choices = []
            for choices_item_data in self.choices:
                choices_item = choices_item_data.to_dict()
                choices.append(choices_item)

        install_url: None | str | Unset
        if isinstance(self.install_url, Unset):
            install_url = UNSET
        else:
            install_url = self.install_url

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
                "wait": wait,
            }
        )
        if choices is not UNSET:
            field_dict["choices"] = choices
        if install_url is not UNSET:
            field_dict["install_url"] = install_url
        if login is not UNSET:
            field_dict["login"] = login
        if manage_url is not UNSET:
            field_dict["manage_url"] = manage_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.github_installation_offer import GithubInstallationOffer

        d = dict(src_dict)
        wait = GithubWait(d.pop("wait"))

        _choices = d.pop("choices", UNSET)
        choices: list[GithubInstallationOffer] | Unset = UNSET
        if _choices is not UNSET:
            choices = []
            for choices_item_data in _choices:
                choices_item = GithubInstallationOffer.from_dict(choices_item_data)

                choices.append(choices_item)

        def _parse_install_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        install_url = _parse_install_url(d.pop("install_url", UNSET))

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

        connection_github = cls(
            wait=wait,
            choices=choices,
            install_url=install_url,
            login=login,
            manage_url=manage_url,
        )

        connection_github.additional_properties = d
        return connection_github

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
