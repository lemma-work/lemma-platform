from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.auth_config_create_schema_config_type_0 import (
        AuthConfigCreateSchemaConfigType0,
    )


T = TypeVar("T", bound="AuthConfigCreateSchema")


@_attrs_define
class AuthConfigCreateSchema:
    """
    Attributes:
        connector_id (str):
        config (AuthConfigCreateSchemaConfigType0 | None | Unset):
        config_source (str | Unset):  Default: 'SYSTEM_DEFAULT'.
        kind (None | str | Unset): Which of the connector's kinds to install. Optional when the connector offers only
            one.
        name (None | str | Unset):
    """

    connector_id: str
    config: AuthConfigCreateSchemaConfigType0 | None | Unset = UNSET
    config_source: str | Unset = "SYSTEM_DEFAULT"
    kind: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.auth_config_create_schema_config_type_0 import (
            AuthConfigCreateSchemaConfigType0,
        )

        connector_id = self.connector_id

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, AuthConfigCreateSchemaConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        config_source = self.config_source

        kind: None | str | Unset
        if isinstance(self.kind, Unset):
            kind = UNSET
        else:
            kind = self.kind

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if config_source is not UNSET:
            field_dict["config_source"] = config_source
        if kind is not UNSET:
            field_dict["kind"] = kind
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_config_create_schema_config_type_0 import (
            AuthConfigCreateSchemaConfigType0,
        )

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        def _parse_config(
            data: object,
        ) -> AuthConfigCreateSchemaConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = AuthConfigCreateSchemaConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AuthConfigCreateSchemaConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        config_source = d.pop("config_source", UNSET)

        def _parse_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        kind = _parse_kind(d.pop("kind", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        auth_config_create_schema = cls(
            connector_id=connector_id,
            config=config,
            config_source=config_source,
            kind=kind,
            name=name,
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
