from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.composio_provider_capability_response_schema import (
        ComposioProviderCapabilityResponseSchema,
    )
    from ..models.connector_detail_response_schema_operations import (
        ConnectorDetailResponseSchemaOperations,
    )
    from ..models.lemma_provider_capability_response_schema import (
        LemmaProviderCapabilityResponseSchema,
    )


T = TypeVar("T", bound="ConnectorDetailResponseSchema")


@_attrs_define
class ConnectorDetailResponseSchema:
    """Schema for connector details including operation catalog.

    Attributes:
        created_at (datetime.datetime):
        description (None | str):
        icon (None | str):
        id (str):
        is_active (bool):
        updated_at (datetime.datetime):
        operations (ConnectorDetailResponseSchemaOperations | Unset):
        provider_capabilities (list[ComposioProviderCapabilityResponseSchema | LemmaProviderCapabilityResponseSchema] |
            Unset):
        title (None | str | Unset):
    """

    created_at: datetime.datetime
    description: None | str
    icon: None | str
    id: str
    is_active: bool
    updated_at: datetime.datetime
    operations: ConnectorDetailResponseSchemaOperations | Unset = UNSET
    provider_capabilities: (
        list[
            ComposioProviderCapabilityResponseSchema
            | LemmaProviderCapabilityResponseSchema
        ]
        | Unset
    ) = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.lemma_provider_capability_response_schema import (
            LemmaProviderCapabilityResponseSchema,
        )

        created_at = self.created_at.isoformat()

        description: None | str
        description = self.description

        icon: None | str
        icon = self.icon

        id = self.id

        is_active = self.is_active

        updated_at = self.updated_at.isoformat()

        operations: dict[str, Any] | Unset = UNSET
        if not isinstance(self.operations, Unset):
            operations = self.operations.to_dict()

        provider_capabilities: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.provider_capabilities, Unset):
            provider_capabilities = []
            for provider_capabilities_item_data in self.provider_capabilities:
                provider_capabilities_item: dict[str, Any]
                if isinstance(
                    provider_capabilities_item_data,
                    LemmaProviderCapabilityResponseSchema,
                ):
                    provider_capabilities_item = (
                        provider_capabilities_item_data.to_dict()
                    )
                else:
                    provider_capabilities_item = (
                        provider_capabilities_item_data.to_dict()
                    )

                provider_capabilities.append(provider_capabilities_item)

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "description": description,
                "icon": icon,
                "id": id,
                "is_active": is_active,
                "updated_at": updated_at,
            }
        )
        if operations is not UNSET:
            field_dict["operations"] = operations
        if provider_capabilities is not UNSET:
            field_dict["provider_capabilities"] = provider_capabilities
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.composio_provider_capability_response_schema import (
            ComposioProviderCapabilityResponseSchema,
        )
        from ..models.connector_detail_response_schema_operations import (
            ConnectorDetailResponseSchemaOperations,
        )
        from ..models.lemma_provider_capability_response_schema import (
            LemmaProviderCapabilityResponseSchema,
        )

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        def _parse_icon(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        icon = _parse_icon(d.pop("icon"))

        id = d.pop("id")

        is_active = d.pop("is_active")

        updated_at = isoparse(d.pop("updated_at"))

        _operations = d.pop("operations", UNSET)
        operations: ConnectorDetailResponseSchemaOperations | Unset
        if isinstance(_operations, Unset):
            operations = UNSET
        else:
            operations = ConnectorDetailResponseSchemaOperations.from_dict(_operations)

        _provider_capabilities = d.pop("provider_capabilities", UNSET)
        provider_capabilities: (
            list[
                ComposioProviderCapabilityResponseSchema
                | LemmaProviderCapabilityResponseSchema
            ]
            | Unset
        ) = UNSET
        if _provider_capabilities is not UNSET:
            provider_capabilities = []
            for provider_capabilities_item_data in _provider_capabilities:

                def _parse_provider_capabilities_item(
                    data: object,
                ) -> (
                    ComposioProviderCapabilityResponseSchema
                    | LemmaProviderCapabilityResponseSchema
                ):
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        provider_capabilities_item_type_0 = (
                            LemmaProviderCapabilityResponseSchema.from_dict(data)
                        )

                        return provider_capabilities_item_type_0
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    provider_capabilities_item_type_1 = (
                        ComposioProviderCapabilityResponseSchema.from_dict(data)
                    )

                    return provider_capabilities_item_type_1

                provider_capabilities_item = _parse_provider_capabilities_item(
                    provider_capabilities_item_data
                )

                provider_capabilities.append(provider_capabilities_item)

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        connector_detail_response_schema = cls(
            created_at=created_at,
            description=description,
            icon=icon,
            id=id,
            is_active=is_active,
            updated_at=updated_at,
            operations=operations,
            provider_capabilities=provider_capabilities,
            title=title,
        )

        connector_detail_response_schema.additional_properties = d
        return connector_detail_response_schema

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
