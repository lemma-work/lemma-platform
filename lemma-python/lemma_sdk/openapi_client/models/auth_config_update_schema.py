from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.auth_config_update_schema_config_type_0 import (
        AuthConfigUpdateSchemaConfigType0,
    )


T = TypeVar("T", bound="AuthConfigUpdateSchema")


@_attrs_define
class AuthConfigUpdateSchema:
    """
    Attributes:
        config (AuthConfigUpdateSchemaConfigType0 | None | Unset): Replacement configuration. Re-validated against the
            connector's schema, and re-checked against the network-target guard.
        is_default (bool | None | Unset): Make this the install that a bare connector_id resolves to. Demotes whichever
            install currently holds that role.
        name (None | str | Unset): New name for this install. Accounts follow the rename, since they reference the
            install by id.
        status (None | str | Unset): ACTIVE or DISABLED.
    """

    config: AuthConfigUpdateSchemaConfigType0 | None | Unset = UNSET
    is_default: bool | None | Unset = UNSET
    name: None | str | Unset = UNSET
    status: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.auth_config_update_schema_config_type_0 import (
            AuthConfigUpdateSchemaConfigType0,
        )

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, AuthConfigUpdateSchemaConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        is_default: bool | None | Unset
        if isinstance(self.is_default, Unset):
            is_default = UNSET
        else:
            is_default = self.is_default

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        else:
            status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if is_default is not UNSET:
            field_dict["is_default"] = is_default
        if name is not UNSET:
            field_dict["name"] = name
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_config_update_schema_config_type_0 import (
            AuthConfigUpdateSchemaConfigType0,
        )

        d = dict(src_dict)

        def _parse_config(
            data: object,
        ) -> AuthConfigUpdateSchemaConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = AuthConfigUpdateSchemaConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AuthConfigUpdateSchemaConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_is_default(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        is_default = _parse_is_default(d.pop("is_default", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        auth_config_update_schema = cls(
            config=config,
            is_default=is_default,
            name=name,
            status=status,
        )

        auth_config_update_schema.additional_properties = d
        return auth_config_update_schema

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
