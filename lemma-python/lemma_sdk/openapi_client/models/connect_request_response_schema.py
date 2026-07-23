from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.connect_request_response_schema_attributes_type_0 import (
        ConnectRequestResponseSchemaAttributesType0,
    )


T = TypeVar("T", bound="ConnectRequestResponseSchema")


@_attrs_define
class ConnectRequestResponseSchema:
    """Schema for connect request response.

    Attributes:
        attributes (ConnectRequestResponseSchemaAttributesType0 | None):
        auth_config_id (UUID):
        authorization_url (None | str):
        connector_id (str):
        created_at (datetime.datetime):
        id (UUID):
        organization_id (UUID):
        status (str):
        updated_at (datetime.datetime):
        user_id (UUID):
    """

    attributes: ConnectRequestResponseSchemaAttributesType0 | None
    auth_config_id: UUID
    authorization_url: None | str
    connector_id: str
    created_at: datetime.datetime
    id: UUID
    organization_id: UUID
    status: str
    updated_at: datetime.datetime
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.connect_request_response_schema_attributes_type_0 import (
            ConnectRequestResponseSchemaAttributesType0,
        )

        attributes: dict[str, Any] | None
        if isinstance(self.attributes, ConnectRequestResponseSchemaAttributesType0):
            attributes = self.attributes.to_dict()
        else:
            attributes = self.attributes

        auth_config_id = str(self.auth_config_id)

        authorization_url: None | str
        authorization_url = self.authorization_url

        connector_id = self.connector_id

        created_at = self.created_at.isoformat()

        id = str(self.id)

        organization_id = str(self.organization_id)

        status = self.status

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attributes": attributes,
                "auth_config_id": auth_config_id,
                "authorization_url": authorization_url,
                "connector_id": connector_id,
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "status": status,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connect_request_response_schema_attributes_type_0 import (
            ConnectRequestResponseSchemaAttributesType0,
        )

        d = dict(src_dict)

        def _parse_attributes(
            data: object,
        ) -> ConnectRequestResponseSchemaAttributesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attributes_type_0 = (
                    ConnectRequestResponseSchemaAttributesType0.from_dict(data)
                )

                return attributes_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ConnectRequestResponseSchemaAttributesType0 | None, data)

        attributes = _parse_attributes(d.pop("attributes"))

        auth_config_id = UUID(d.pop("auth_config_id"))

        def _parse_authorization_url(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        authorization_url = _parse_authorization_url(d.pop("authorization_url"))

        connector_id = d.pop("connector_id")

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        organization_id = UUID(d.pop("organization_id"))

        status = d.pop("status")

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        connect_request_response_schema = cls(
            attributes=attributes,
            auth_config_id=auth_config_id,
            authorization_url=authorization_url,
            connector_id=connector_id,
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            status=status,
            updated_at=updated_at,
            user_id=user_id,
        )

        connect_request_response_schema.additional_properties = d
        return connect_request_response_schema

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
