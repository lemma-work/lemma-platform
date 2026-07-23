from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.auth_config_create_schema_credential_config_type_0 import (
        AuthConfigCreateSchemaCredentialConfigType0,
    )


T = TypeVar("T", bound="AuthConfigCreateSchema")


@_attrs_define
class AuthConfigCreateSchema:
    """
    Attributes:
        connector_id (str):
        config_source (str | Unset):  Default: 'SYSTEM_DEFAULT'.
        credential_config (AuthConfigCreateSchemaCredentialConfigType0 | None | Unset):
        name (None | str | Unset):
        provider (str | Unset):  Default: 'LEMMA'.
    """

    connector_id: str
    config_source: str | Unset = "SYSTEM_DEFAULT"
    credential_config: AuthConfigCreateSchemaCredentialConfigType0 | None | Unset = (
        UNSET
    )
    name: None | str | Unset = UNSET
    provider: str | Unset = "LEMMA"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.auth_config_create_schema_credential_config_type_0 import (
            AuthConfigCreateSchemaCredentialConfigType0,
        )

        connector_id = self.connector_id

        config_source = self.config_source

        credential_config: dict[str, Any] | None | Unset
        if isinstance(self.credential_config, Unset):
            credential_config = UNSET
        elif isinstance(
            self.credential_config, AuthConfigCreateSchemaCredentialConfigType0
        ):
            credential_config = self.credential_config.to_dict()
        else:
            credential_config = self.credential_config

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        provider = self.provider

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
            }
        )
        if config_source is not UNSET:
            field_dict["config_source"] = config_source
        if credential_config is not UNSET:
            field_dict["credential_config"] = credential_config
        if name is not UNSET:
            field_dict["name"] = name
        if provider is not UNSET:
            field_dict["provider"] = provider

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_config_create_schema_credential_config_type_0 import (
            AuthConfigCreateSchemaCredentialConfigType0,
        )

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        config_source = d.pop("config_source", UNSET)

        def _parse_credential_config(
            data: object,
        ) -> AuthConfigCreateSchemaCredentialConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_config_type_0 = (
                    AuthConfigCreateSchemaCredentialConfigType0.from_dict(data)
                )

                return credential_config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                AuthConfigCreateSchemaCredentialConfigType0 | None | Unset, data
            )

        credential_config = _parse_credential_config(d.pop("credential_config", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        provider = d.pop("provider", UNSET)

        auth_config_create_schema = cls(
            connector_id=connector_id,
            config_source=config_source,
            credential_config=credential_config,
            name=name,
            provider=provider,
        )

        auth_config_create_schema.additional_properties = d
        return auth_config_create_schema

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
