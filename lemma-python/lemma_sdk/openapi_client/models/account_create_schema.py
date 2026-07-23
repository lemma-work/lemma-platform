from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.account_create_schema_credentials import (
        AccountCreateSchemaCredentials,
    )
    from ..models.account_create_schema_preferences_type_0 import (
        AccountCreateSchemaPreferencesType0,
    )


T = TypeVar("T", bound="AccountCreateSchema")


@_attrs_define
class AccountCreateSchema:
    """Schema for directly connecting a credential-managed native account.

    Attributes:
        allowed_scopes (list[str] | None | Unset):
        auth_config_id (None | Unset | UUID): Auth config ID to connect
        auth_config_name (None | str | Unset): Auth config name to connect
        credentials (AccountCreateSchemaCredentials | Unset):
        email (None | str | Unset):
        preferences (AccountCreateSchemaPreferencesType0 | None | Unset):
        provider_account_id (None | str | Unset):
    """

    allowed_scopes: list[str] | None | Unset = UNSET
    auth_config_id: None | Unset | UUID = UNSET
    auth_config_name: None | str | Unset = UNSET
    credentials: AccountCreateSchemaCredentials | Unset = UNSET
    email: None | str | Unset = UNSET
    preferences: AccountCreateSchemaPreferencesType0 | None | Unset = UNSET
    provider_account_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.account_create_schema_preferences_type_0 import (
            AccountCreateSchemaPreferencesType0,
        )

        allowed_scopes: list[str] | None | Unset
        if isinstance(self.allowed_scopes, Unset):
            allowed_scopes = UNSET
        elif isinstance(self.allowed_scopes, list):
            allowed_scopes = self.allowed_scopes

        else:
            allowed_scopes = self.allowed_scopes

        auth_config_id: None | str | Unset
        if isinstance(self.auth_config_id, Unset):
            auth_config_id = UNSET
        elif isinstance(self.auth_config_id, UUID):
            auth_config_id = str(self.auth_config_id)
        else:
            auth_config_id = self.auth_config_id

        auth_config_name: None | str | Unset
        if isinstance(self.auth_config_name, Unset):
            auth_config_name = UNSET
        else:
            auth_config_name = self.auth_config_name

        credentials: dict[str, Any] | Unset = UNSET
        if not isinstance(self.credentials, Unset):
            credentials = self.credentials.to_dict()

        email: None | str | Unset
        if isinstance(self.email, Unset):
            email = UNSET
        else:
            email = self.email

        preferences: dict[str, Any] | None | Unset
        if isinstance(self.preferences, Unset):
            preferences = UNSET
        elif isinstance(self.preferences, AccountCreateSchemaPreferencesType0):
            preferences = self.preferences.to_dict()
        else:
            preferences = self.preferences

        provider_account_id: None | str | Unset
        if isinstance(self.provider_account_id, Unset):
            provider_account_id = UNSET
        else:
            provider_account_id = self.provider_account_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if allowed_scopes is not UNSET:
            field_dict["allowed_scopes"] = allowed_scopes
        if auth_config_id is not UNSET:
            field_dict["auth_config_id"] = auth_config_id
        if auth_config_name is not UNSET:
            field_dict["auth_config_name"] = auth_config_name
        if credentials is not UNSET:
            field_dict["credentials"] = credentials
        if email is not UNSET:
            field_dict["email"] = email
        if preferences is not UNSET:
            field_dict["preferences"] = preferences
        if provider_account_id is not UNSET:
            field_dict["provider_account_id"] = provider_account_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_create_schema_credentials import (
            AccountCreateSchemaCredentials,
        )
        from ..models.account_create_schema_preferences_type_0 import (
            AccountCreateSchemaPreferencesType0,
        )

        d = dict(src_dict)

        def _parse_allowed_scopes(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                allowed_scopes_type_0 = cast(list[str], data)

                return allowed_scopes_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        allowed_scopes = _parse_allowed_scopes(d.pop("allowed_scopes", UNSET))

        def _parse_auth_config_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                auth_config_id_type_0 = UUID(data)

                return auth_config_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        auth_config_id = _parse_auth_config_id(d.pop("auth_config_id", UNSET))

        def _parse_auth_config_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_config_name = _parse_auth_config_name(d.pop("auth_config_name", UNSET))

        _credentials = d.pop("credentials", UNSET)
        credentials: AccountCreateSchemaCredentials | Unset
        if isinstance(_credentials, Unset):
            credentials = UNSET
        else:
            credentials = AccountCreateSchemaCredentials.from_dict(_credentials)

        def _parse_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email = _parse_email(d.pop("email", UNSET))

        def _parse_preferences(
            data: object,
        ) -> AccountCreateSchemaPreferencesType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                preferences_type_0 = AccountCreateSchemaPreferencesType0.from_dict(data)

                return preferences_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AccountCreateSchemaPreferencesType0 | None | Unset, data)

        preferences = _parse_preferences(d.pop("preferences", UNSET))

        def _parse_provider_account_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider_account_id = _parse_provider_account_id(
            d.pop("provider_account_id", UNSET)
        )

        account_create_schema = cls(
            allowed_scopes=allowed_scopes,
            auth_config_id=auth_config_id,
            auth_config_name=auth_config_name,
            credentials=credentials,
            email=email,
            preferences=preferences,
            provider_account_id=provider_account_id,
        )

        account_create_schema.additional_properties = d
        return account_create_schema

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
