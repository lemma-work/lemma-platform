from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectRequestInitiateSchema")


@_attrs_define
class ConnectRequestInitiateSchema:
    """Schema for initiating a connect request.

    Attributes:
        auth_config_id (None | Unset | UUID): Auth config ID to connect
        connector_id (None | str | Unset): Connector ID to connect
    """

    auth_config_id: None | Unset | UUID = UNSET
    connector_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_config_id: None | str | Unset
        if isinstance(self.auth_config_id, Unset):
            auth_config_id = UNSET
        elif isinstance(self.auth_config_id, UUID):
            auth_config_id = str(self.auth_config_id)
        else:
            auth_config_id = self.auth_config_id

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if auth_config_id is not UNSET:
            field_dict["auth_config_id"] = auth_config_id
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

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

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        connect_request_initiate_schema = cls(
            auth_config_id=auth_config_id,
            connector_id=connector_id,
        )

        connect_request_initiate_schema.additional_properties = d
        return connect_request_initiate_schema

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
