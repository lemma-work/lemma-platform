from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_detail_response_schema_operations import (
        ConnectorDetailResponseSchemaOperations,
    )
    from ..models.connector_kind_response_schema import ConnectorKindResponseSchema


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
        kinds (list[ConnectorKindResponseSchema] | Unset):
        operations (ConnectorDetailResponseSchemaOperations | Unset):
        title (None | str | Unset):
    """

    created_at: datetime.datetime
    description: None | str
    icon: None | str
    id: str
    is_active: bool
    updated_at: datetime.datetime
    kinds: list[ConnectorKindResponseSchema] | Unset = UNSET
    operations: ConnectorDetailResponseSchemaOperations | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        description: None | str
        description = self.description

        icon: None | str
        icon = self.icon

        id = self.id

        is_active = self.is_active

        updated_at = self.updated_at.isoformat()

        kinds: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.kinds, Unset):
            kinds = []
            for kinds_item_data in self.kinds:
                kinds_item = kinds_item_data.to_dict()
                kinds.append(kinds_item)

        operations: dict[str, Any] | Unset = UNSET
        if not isinstance(self.operations, Unset):
            operations = self.operations.to_dict()

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
        if kinds is not UNSET:
            field_dict["kinds"] = kinds
        if operations is not UNSET:
            field_dict["operations"] = operations
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_detail_response_schema_operations import (
            ConnectorDetailResponseSchemaOperations,
        )
        from ..models.connector_kind_response_schema import ConnectorKindResponseSchema

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

        _kinds = d.pop("kinds", UNSET)
        kinds: list[ConnectorKindResponseSchema] | Unset = UNSET
        if _kinds is not UNSET:
            kinds = []
            for kinds_item_data in _kinds:
                kinds_item = ConnectorKindResponseSchema.from_dict(kinds_item_data)

                kinds.append(kinds_item)

        _operations = d.pop("operations", UNSET)
        operations: ConnectorDetailResponseSchemaOperations | Unset
        if isinstance(_operations, Unset):
            operations = UNSET
        else:
            operations = ConnectorDetailResponseSchemaOperations.from_dict(_operations)

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
            kinds=kinds,
            operations=operations,
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
