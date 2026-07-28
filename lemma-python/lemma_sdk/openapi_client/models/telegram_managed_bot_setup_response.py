from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TelegramManagedBotSetupResponse")


@_attrs_define
class TelegramManagedBotSetupResponse:
    """
    Attributes:
        expires_at (str):
        launch_url (str):
        manager_bot_username (str):
        setup_id (str):
        status (str):
        account_id (None | Unset | UUID):
        bot_launch_url (None | str | Unset):
        bot_username (None | str | Unset):
        error (None | str | Unset):
        surface_id (None | Unset | UUID):
    """

    expires_at: str
    launch_url: str
    manager_bot_username: str
    setup_id: str
    status: str
    account_id: None | Unset | UUID = UNSET
    bot_launch_url: None | str | Unset = UNSET
    bot_username: None | str | Unset = UNSET
    error: None | str | Unset = UNSET
    surface_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires_at = self.expires_at

        launch_url = self.launch_url

        manager_bot_username = self.manager_bot_username

        setup_id = self.setup_id

        status = self.status

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        bot_launch_url: None | str | Unset
        if isinstance(self.bot_launch_url, Unset):
            bot_launch_url = UNSET
        else:
            bot_launch_url = self.bot_launch_url

        bot_username: None | str | Unset
        if isinstance(self.bot_username, Unset):
            bot_username = UNSET
        else:
            bot_username = self.bot_username

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        surface_id: None | str | Unset
        if isinstance(self.surface_id, Unset):
            surface_id = UNSET
        elif isinstance(self.surface_id, UUID):
            surface_id = str(self.surface_id)
        else:
            surface_id = self.surface_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires_at": expires_at,
                "launch_url": launch_url,
                "manager_bot_username": manager_bot_username,
                "setup_id": setup_id,
                "status": status,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if bot_launch_url is not UNSET:
            field_dict["bot_launch_url"] = bot_launch_url
        if bot_username is not UNSET:
            field_dict["bot_username"] = bot_username
        if error is not UNSET:
            field_dict["error"] = error
        if surface_id is not UNSET:
            field_dict["surface_id"] = surface_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expires_at = d.pop("expires_at")

        launch_url = d.pop("launch_url")

        manager_bot_username = d.pop("manager_bot_username")

        setup_id = d.pop("setup_id")

        status = d.pop("status")

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_bot_launch_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        bot_launch_url = _parse_bot_launch_url(d.pop("bot_launch_url", UNSET))

        def _parse_bot_username(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        bot_username = _parse_bot_username(d.pop("bot_username", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_surface_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                surface_id_type_0 = UUID(data)

                return surface_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        surface_id = _parse_surface_id(d.pop("surface_id", UNSET))

        telegram_managed_bot_setup_response = cls(
            expires_at=expires_at,
            launch_url=launch_url,
            manager_bot_username=manager_bot_username,
            setup_id=setup_id,
            status=status,
            account_id=account_id,
            bot_launch_url=bot_launch_url,
            bot_username=bot_username,
            error=error,
            surface_id=surface_id,
        )

        telegram_managed_bot_setup_response.additional_properties = d
        return telegram_managed_bot_setup_response

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
