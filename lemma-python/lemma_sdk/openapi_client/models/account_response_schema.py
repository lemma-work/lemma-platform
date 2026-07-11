from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.account_response_schema_preferences_type_0 import (
        AccountResponseSchemaPreferencesType0,
    )
    from ..models.connector_response_schema import ConnectorResponseSchema


T = TypeVar("T", bound="AccountResponseSchema")


@_attrs_define
class AccountResponseSchema:
    """Schema for account response.

    Attributes:
        allowed_scopes (list[str] | None):
        auth_config_id (UUID):
        connector_id (str):
        created_at (datetime.datetime):
        email (None | str):
        id (UUID):
        organization_id (UUID):
        preferences (AccountResponseSchemaPreferencesType0 | None):
        status (str):
        updated_at (datetime.datetime):
        user_id (UUID):
        connector (ConnectorResponseSchema | None | Unset):
        display_name (None | str | Unset):
        is_default (bool | Unset):  Default: False.
        provider (None | str | Unset):
        provider_account_id (None | str | Unset):
    """

    allowed_scopes: list[str] | None
    auth_config_id: UUID
    connector_id: str
    created_at: datetime.datetime
    email: None | str
    id: UUID
    organization_id: UUID
    preferences: AccountResponseSchemaPreferencesType0 | None
    status: str
    updated_at: datetime.datetime
    user_id: UUID
    connector: ConnectorResponseSchema | None | Unset = UNSET
    display_name: None | str | Unset = UNSET
    is_default: bool | Unset = False
    provider: None | str | Unset = UNSET
    provider_account_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.account_response_schema_preferences_type_0 import (
            AccountResponseSchemaPreferencesType0,
        )
        from ..models.connector_response_schema import ConnectorResponseSchema

        allowed_scopes: list[str] | None
        if isinstance(self.allowed_scopes, list):
            allowed_scopes = self.allowed_scopes

        else:
            allowed_scopes = self.allowed_scopes

        auth_config_id = str(self.auth_config_id)

        connector_id = self.connector_id

        created_at = self.created_at.isoformat()

        email: None | str
        email = self.email

        id = str(self.id)

        organization_id = str(self.organization_id)

        preferences: dict[str, Any] | None
        if isinstance(self.preferences, AccountResponseSchemaPreferencesType0):
            preferences = self.preferences.to_dict()
        else:
            preferences = self.preferences

        status = self.status

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        connector: dict[str, Any] | None | Unset
        if isinstance(self.connector, Unset):
            connector = UNSET
        elif isinstance(self.connector, ConnectorResponseSchema):
            connector = self.connector.to_dict()
        else:
            connector = self.connector

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        is_default = self.is_default

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        provider_account_id: None | str | Unset
        if isinstance(self.provider_account_id, Unset):
            provider_account_id = UNSET
        else:
            provider_account_id = self.provider_account_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed_scopes": allowed_scopes,
                "auth_config_id": auth_config_id,
                "connector_id": connector_id,
                "created_at": created_at,
                "email": email,
                "id": id,
                "organization_id": organization_id,
                "preferences": preferences,
                "status": status,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if connector is not UNSET:
            field_dict["connector"] = connector
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if is_default is not UNSET:
            field_dict["is_default"] = is_default
        if provider is not UNSET:
            field_dict["provider"] = provider
        if provider_account_id is not UNSET:
            field_dict["provider_account_id"] = provider_account_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_response_schema_preferences_type_0 import (
            AccountResponseSchemaPreferencesType0,
        )
        from ..models.connector_response_schema import ConnectorResponseSchema

        d = dict(src_dict)

        def _parse_allowed_scopes(data: object) -> list[str] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                allowed_scopes_type_0 = cast(list[str], data)

                return allowed_scopes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None, data)

        allowed_scopes = _parse_allowed_scopes(d.pop("allowed_scopes"))

        auth_config_id = UUID(d.pop("auth_config_id"))

        connector_id = d.pop("connector_id")

        created_at = isoparse(d.pop("created_at"))

        def _parse_email(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        email = _parse_email(d.pop("email"))

        id = UUID(d.pop("id"))

        organization_id = UUID(d.pop("organization_id"))

        def _parse_preferences(
            data: object,
        ) -> AccountResponseSchemaPreferencesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                preferences_type_0 = AccountResponseSchemaPreferencesType0.from_dict(
                    data
                )

                return preferences_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AccountResponseSchemaPreferencesType0 | None, data)

        preferences = _parse_preferences(d.pop("preferences"))

        status = d.pop("status")

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        def _parse_connector(data: object) -> ConnectorResponseSchema | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                connector_type_0 = ConnectorResponseSchema.from_dict(data)

                return connector_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectorResponseSchema | None | Unset, data)

        connector = _parse_connector(d.pop("connector", UNSET))

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        is_default = d.pop("is_default", UNSET)

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        def _parse_provider_account_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider_account_id = _parse_provider_account_id(
            d.pop("provider_account_id", UNSET)
        )

        account_response_schema = cls(
            allowed_scopes=allowed_scopes,
            auth_config_id=auth_config_id,
            connector_id=connector_id,
            created_at=created_at,
            email=email,
            id=id,
            organization_id=organization_id,
            preferences=preferences,
            status=status,
            updated_at=updated_at,
            user_id=user_id,
            connector=connector,
            display_name=display_name,
            is_default=is_default,
            provider=provider,
            provider_account_id=provider_account_id,
        )

        account_response_schema.additional_properties = d
        return account_response_schema

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
