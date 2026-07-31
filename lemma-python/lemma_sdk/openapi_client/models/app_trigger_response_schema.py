from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.auth_provider import AuthProvider

if TYPE_CHECKING:
    from ..models.app_trigger_response_schema_config_schema_type_0 import (
        AppTriggerResponseSchemaConfigSchemaType0,
    )
    from ..models.app_trigger_response_schema_payload_example_type_0 import (
        AppTriggerResponseSchemaPayloadExampleType0,
    )
    from ..models.app_trigger_response_schema_payload_schema_type_0 import (
        AppTriggerResponseSchemaPayloadSchemaType0,
    )


T = TypeVar("T", bound="AppTriggerResponseSchema")


@_attrs_define
class AppTriggerResponseSchema:
    """Schema for trigger response.

    Attributes:
        config_schema (AppTriggerResponseSchemaConfigSchemaType0 | None):
        connector_id (None | str):
        created_at (datetime.datetime):
        description (None | str):
        id (str):
        payload_example (AppTriggerResponseSchemaPayloadExampleType0 | None):
        payload_schema (AppTriggerResponseSchemaPayloadSchemaType0 | None):
        provider (AuthProvider):
        updated_at (datetime.datetime):
    """

    config_schema: AppTriggerResponseSchemaConfigSchemaType0 | None
    connector_id: None | str
    created_at: datetime.datetime
    description: None | str
    id: str
    payload_example: AppTriggerResponseSchemaPayloadExampleType0 | None
    payload_schema: AppTriggerResponseSchemaPayloadSchemaType0 | None
    provider: AuthProvider
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.app_trigger_response_schema_config_schema_type_0 import (
            AppTriggerResponseSchemaConfigSchemaType0,
        )
        from ..models.app_trigger_response_schema_payload_example_type_0 import (
            AppTriggerResponseSchemaPayloadExampleType0,
        )
        from ..models.app_trigger_response_schema_payload_schema_type_0 import (
            AppTriggerResponseSchemaPayloadSchemaType0,
        )

        config_schema: dict[str, Any] | None
        if isinstance(self.config_schema, AppTriggerResponseSchemaConfigSchemaType0):
            config_schema = self.config_schema.to_dict()
        else:
            config_schema = self.config_schema

        connector_id: None | str
        connector_id = self.connector_id

        created_at = self.created_at.isoformat()

        description: None | str
        description = self.description

        id = self.id

        payload_example: dict[str, Any] | None
        if isinstance(
            self.payload_example, AppTriggerResponseSchemaPayloadExampleType0
        ):
            payload_example = self.payload_example.to_dict()
        else:
            payload_example = self.payload_example

        payload_schema: dict[str, Any] | None
        if isinstance(self.payload_schema, AppTriggerResponseSchemaPayloadSchemaType0):
            payload_schema = self.payload_schema.to_dict()
        else:
            payload_schema = self.payload_schema

        provider = self.provider.value

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config_schema": config_schema,
                "connector_id": connector_id,
                "created_at": created_at,
                "description": description,
                "id": id,
                "payload_example": payload_example,
                "payload_schema": payload_schema,
                "provider": provider,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.app_trigger_response_schema_config_schema_type_0 import (
            AppTriggerResponseSchemaConfigSchemaType0,
        )
        from ..models.app_trigger_response_schema_payload_example_type_0 import (
            AppTriggerResponseSchemaPayloadExampleType0,
        )
        from ..models.app_trigger_response_schema_payload_schema_type_0 import (
            AppTriggerResponseSchemaPayloadSchemaType0,
        )

        d = dict(src_dict)

        def _parse_config_schema(
            data: object,
        ) -> AppTriggerResponseSchemaConfigSchemaType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_schema_type_0 = (
                    AppTriggerResponseSchemaConfigSchemaType0.from_dict(data)
                )

                return config_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AppTriggerResponseSchemaConfigSchemaType0 | None, data)

        config_schema = _parse_config_schema(d.pop("config_schema"))

        def _parse_connector_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        connector_id = _parse_connector_id(d.pop("connector_id"))

        created_at = isoparse(d.pop("created_at"))

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        id = d.pop("id")

        def _parse_payload_example(
            data: object,
        ) -> AppTriggerResponseSchemaPayloadExampleType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                payload_example_type_0 = (
                    AppTriggerResponseSchemaPayloadExampleType0.from_dict(data)
                )

                return payload_example_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AppTriggerResponseSchemaPayloadExampleType0 | None, data)

        payload_example = _parse_payload_example(d.pop("payload_example"))

        def _parse_payload_schema(
            data: object,
        ) -> AppTriggerResponseSchemaPayloadSchemaType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                payload_schema_type_0 = (
                    AppTriggerResponseSchemaPayloadSchemaType0.from_dict(data)
                )

                return payload_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AppTriggerResponseSchemaPayloadSchemaType0 | None, data)

        payload_schema = _parse_payload_schema(d.pop("payload_schema"))

        provider = AuthProvider(d.pop("provider"))

        updated_at = isoparse(d.pop("updated_at"))

        app_trigger_response_schema = cls(
            config_schema=config_schema,
            connector_id=connector_id,
            created_at=created_at,
            description=description,
            id=id,
            payload_example=payload_example,
            payload_schema=payload_schema,
            provider=provider,
            updated_at=updated_at,
        )

        app_trigger_response_schema.additional_properties = d
        return app_trigger_response_schema

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
