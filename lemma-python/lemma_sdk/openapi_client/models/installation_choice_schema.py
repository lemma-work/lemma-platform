from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InstallationChoiceSchema")


@_attrs_define
class InstallationChoiceSchema:
    """One installation an account could speak for.

    Attributes:
        installation_id (str):
        manage_url (str): Where the person changes this installation's repository access. A link rather than an API
            call: the endpoints that add or remove a repository accept only classic personal access tokens.
        account_login (None | str | Unset):
        account_type (None | str | Unset):
        repository_selection (None | str | Unset): 'all' or 'selected'. A selected installation does not pick up newly
            created repositories, which is the commonest reason a repository is missing.
    """

    installation_id: str
    manage_url: str
    account_login: None | str | Unset = UNSET
    account_type: None | str | Unset = UNSET
    repository_selection: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        installation_id = self.installation_id

        manage_url = self.manage_url

        account_login: None | str | Unset
        if isinstance(self.account_login, Unset):
            account_login = UNSET
        else:
            account_login = self.account_login

        account_type: None | str | Unset
        if isinstance(self.account_type, Unset):
            account_type = UNSET
        else:
            account_type = self.account_type

        repository_selection: None | str | Unset
        if isinstance(self.repository_selection, Unset):
            repository_selection = UNSET
        else:
            repository_selection = self.repository_selection

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "installation_id": installation_id,
                "manage_url": manage_url,
            }
        )
        if account_login is not UNSET:
            field_dict["account_login"] = account_login
        if account_type is not UNSET:
            field_dict["account_type"] = account_type
        if repository_selection is not UNSET:
            field_dict["repository_selection"] = repository_selection

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        installation_id = d.pop("installation_id")

        manage_url = d.pop("manage_url")

        def _parse_account_login(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        account_login = _parse_account_login(d.pop("account_login", UNSET))

        def _parse_account_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        account_type = _parse_account_type(d.pop("account_type", UNSET))

        def _parse_repository_selection(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repository_selection = _parse_repository_selection(
            d.pop("repository_selection", UNSET)
        )

        installation_choice_schema = cls(
            installation_id=installation_id,
            manage_url=manage_url,
            account_login=account_login,
            account_type=account_type,
            repository_selection=repository_selection,
        )

        installation_choice_schema.additional_properties = d
        return installation_choice_schema

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
