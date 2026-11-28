from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.installation_choice_schema import InstallationChoiceSchema


T = TypeVar("T", bound="AccountInstallationsSchema")


@_attrs_define
class AccountInstallationsSchema:
    """What an account can actually reach, asked of the provider.

    Attributes:
        install_state (str):
        choices (list[InstallationChoiceSchema] | Unset):
        installation_id (None | str | Unset):
        waiting_on (None | str | Unset): With PENDING_APPROVAL, whose act it waits on: `github_admin` (an admin of the
            GitHub account it is installed on), `github_permission` (an owner accepting the App's Members: read on GitHub),
            or `lemma_admin` (someone who may install apps for this organization).
    """

    install_state: str
    choices: list[InstallationChoiceSchema] | Unset = UNSET
    installation_id: None | str | Unset = UNSET
    waiting_on: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        install_state = self.install_state

        choices: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.choices, Unset):
            choices = []
            for choices_item_data in self.choices:
                choices_item = choices_item_data.to_dict()
                choices.append(choices_item)

        installation_id: None | str | Unset
        if isinstance(self.installation_id, Unset):
            installation_id = UNSET
        else:
            installation_id = self.installation_id

        waiting_on: None | str | Unset
        if isinstance(self.waiting_on, Unset):
            waiting_on = UNSET
        else:
            waiting_on = self.waiting_on

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "install_state": install_state,
            }
        )
        if choices is not UNSET:
            field_dict["choices"] = choices
        if installation_id is not UNSET:
            field_dict["installation_id"] = installation_id
        if waiting_on is not UNSET:
            field_dict["waiting_on"] = waiting_on

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.installation_choice_schema import InstallationChoiceSchema

        d = dict(src_dict)
        install_state = d.pop("install_state")

        _choices = d.pop("choices", UNSET)
        choices: list[InstallationChoiceSchema] | Unset = UNSET
        if _choices is not UNSET:
            choices = []
            for choices_item_data in _choices:
                choices_item = InstallationChoiceSchema.from_dict(choices_item_data)

                choices.append(choices_item)

        def _parse_installation_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        installation_id = _parse_installation_id(d.pop("installation_id", UNSET))

        def _parse_waiting_on(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        waiting_on = _parse_waiting_on(d.pop("waiting_on", UNSET))

        account_installations_schema = cls(
            install_state=install_state,
            choices=choices,
            installation_id=installation_id,
            waiting_on=waiting_on,
        )

        account_installations_schema.additional_properties = d
        return account_installations_schema

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
