from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.auth_provider import AuthProvider

T = TypeVar("T", bound="AppTriggerSummaryResponseSchema")


@_attrs_define
class AppTriggerSummaryResponseSchema:
    """Lean trigger shape for list responses.

    Omits the heavy `config_schema` / `payload_schema` / `payload_example` JSON
    blobs — fetch those from `connector.trigger.get`.

        Attributes:
            connector_id (None | str):
            created_at (datetime.datetime):
            description (None | str):
            id (str):
            provider (AuthProvider):
            updated_at (datetime.datetime):
    """

    connector_id: None | str
    created_at: datetime.datetime
    description: None | str
    id: str
    provider: AuthProvider
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connector_id: None | str
        connector_id = self.connector_id

        created_at = self.created_at.isoformat()

        description: None | str
        description = self.description

        id = self.id

        provider = self.provider.value

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
                "created_at": created_at,
                "description": description,
                "id": id,
                "provider": provider,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

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

        provider = AuthProvider(d.pop("provider"))

        updated_at = isoparse(d.pop("updated_at"))

        app_trigger_summary_response_schema = cls(
            connector_id=connector_id,
            created_at=created_at,
            description=description,
            id=id,
            provider=provider,
            updated_at=updated_at,
        )

        app_trigger_summary_response_schema.additional_properties = d
        return app_trigger_summary_response_schema

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
