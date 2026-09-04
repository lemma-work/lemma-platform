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
    from ..models.auth_config_response_schema_config_type_0 import (
        AuthConfigResponseSchemaConfigType0,
    )
    from ..models.auth_config_response_schema_metadata_type_0 import (
        AuthConfigResponseSchemaMetadataType0,
    )


T = TypeVar("T", bound="AuthConfigResponseSchema")


@_attrs_define
class AuthConfigResponseSchema:
    """
    Attributes:
        config_source (str):
        connector_id (str):
        created_at (datetime.datetime):
        id (UUID):
        kind (str):
        name (str):
        organization_id (UUID):
        status (str):
        updated_at (datetime.datetime):
        auth_scheme (None | str | Unset): How this install authenticates, which is not always what the connector's
            catalog entry says. `mcp` is one catalog entry standing for every server a tenant may point at: the entry says
            API_KEY, but an install whose server described its own authorization when it was created signs in through a
            browser and answers OAUTH2 here. Branch on this rather than on the connector's kind when deciding how to connect
            an install.
        config (AuthConfigResponseSchemaConfigType0 | None | Unset):
        is_default (bool | Unset):  Default: False.
        metadata (AuthConfigResponseSchemaMetadataType0 | None | Unset):
    """

    config_source: str
    connector_id: str
    created_at: datetime.datetime
    id: UUID
    kind: str
    name: str
    organization_id: UUID
    status: str
    updated_at: datetime.datetime
    auth_scheme: None | str | Unset = UNSET
    config: AuthConfigResponseSchemaConfigType0 | None | Unset = UNSET
    is_default: bool | Unset = False
    metadata: AuthConfigResponseSchemaMetadataType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.auth_config_response_schema_config_type_0 import (
            AuthConfigResponseSchemaConfigType0,
        )
        from ..models.auth_config_response_schema_metadata_type_0 import (
            AuthConfigResponseSchemaMetadataType0,
        )

        config_source = self.config_source

        connector_id = self.connector_id

        created_at = self.created_at.isoformat()

        id = str(self.id)

        kind = self.kind

        name = self.name

        organization_id = str(self.organization_id)

        status = self.status

        updated_at = self.updated_at.isoformat()

        auth_scheme: None | str | Unset
        if isinstance(self.auth_scheme, Unset):
            auth_scheme = UNSET
        else:
            auth_scheme = self.auth_scheme

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, AuthConfigResponseSchemaConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        is_default = self.is_default

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, AuthConfigResponseSchemaMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config_source": config_source,
                "connector_id": connector_id,
                "created_at": created_at,
                "id": id,
                "kind": kind,
                "name": name,
                "organization_id": organization_id,
                "status": status,
                "updated_at": updated_at,
            }
        )
        if auth_scheme is not UNSET:
            field_dict["auth_scheme"] = auth_scheme
        if config is not UNSET:
            field_dict["config"] = config
        if is_default is not UNSET:
            field_dict["is_default"] = is_default
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_config_response_schema_config_type_0 import (
            AuthConfigResponseSchemaConfigType0,
        )
        from ..models.auth_config_response_schema_metadata_type_0 import (
            AuthConfigResponseSchemaMetadataType0,
        )

        d = dict(src_dict)
        config_source = d.pop("config_source")

        connector_id = d.pop("connector_id")

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        kind = d.pop("kind")

        name = d.pop("name")

        organization_id = UUID(d.pop("organization_id"))

        status = d.pop("status")

        updated_at = isoparse(d.pop("updated_at"))

        def _parse_auth_scheme(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_scheme = _parse_auth_scheme(d.pop("auth_scheme", UNSET))

        def _parse_config(
            data: object,
        ) -> AuthConfigResponseSchemaConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = AuthConfigResponseSchemaConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AuthConfigResponseSchemaConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        is_default = d.pop("is_default", UNSET)

        def _parse_metadata(
            data: object,
        ) -> AuthConfigResponseSchemaMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = AuthConfigResponseSchemaMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AuthConfigResponseSchemaMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        auth_config_response_schema = cls(
            config_source=config_source,
            connector_id=connector_id,
            created_at=created_at,
            id=id,
            kind=kind,
            name=name,
            organization_id=organization_id,
            status=status,
            updated_at=updated_at,
            auth_scheme=auth_scheme,
            config=config,
            is_default=is_default,
            metadata=metadata,
        )

        auth_config_response_schema.additional_properties = d
        return auth_config_response_schema

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
