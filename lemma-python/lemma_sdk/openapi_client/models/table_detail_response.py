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
    from ..models.column_schema import ColumnSchema
    from ..models.table_detail_response_config_type_0 import (
        TableDetailResponseConfigType0,
    )


T = TypeVar("T", bound="TableDetailResponse")


@_attrs_define
class TableDetailResponse:
    """Schema for table detail response.

    Attributes:
        columns (list[ColumnSchema]):
        config (None | TableDetailResponseConfigType0):
        created_at (datetime.datetime):
        enable_rls (bool):
        id (UUID):
        name (str):
        pod_id (UUID):
        primary_key_column (str):
        updated_at (datetime.datetime):
        allowed_actions (list[str] | Unset):
        visibility (str | Unset):  Default: 'POD'.
    """

    columns: list[ColumnSchema]
    config: None | TableDetailResponseConfigType0
    created_at: datetime.datetime
    enable_rls: bool
    id: UUID
    name: str
    pod_id: UUID
    primary_key_column: str
    updated_at: datetime.datetime
    allowed_actions: list[str] | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.table_detail_response_config_type_0 import (
            TableDetailResponseConfigType0,
        )

        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        config: dict[str, Any] | None
        if isinstance(self.config, TableDetailResponseConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        created_at = self.created_at.isoformat()

        enable_rls = self.enable_rls

        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        primary_key_column = self.primary_key_column

        updated_at = self.updated_at.isoformat()

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "config": config,
                "created_at": created_at,
                "enable_rls": enable_rls,
                "id": id,
                "name": name,
                "pod_id": pod_id,
                "primary_key_column": primary_key_column,
                "updated_at": updated_at,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.column_schema import ColumnSchema
        from ..models.table_detail_response_config_type_0 import (
            TableDetailResponseConfigType0,
        )

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = ColumnSchema.from_dict(columns_item_data)

            columns.append(columns_item)

        def _parse_config(data: object) -> None | TableDetailResponseConfigType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = TableDetailResponseConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | TableDetailResponseConfigType0, data)

        config = _parse_config(d.pop("config"))

        created_at = isoparse(d.pop("created_at"))

        enable_rls = d.pop("enable_rls")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        primary_key_column = d.pop("primary_key_column")

        updated_at = isoparse(d.pop("updated_at"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        visibility = d.pop("visibility", UNSET)

        table_detail_response = cls(
            columns=columns,
            config=config,
            created_at=created_at,
            enable_rls=enable_rls,
            id=id,
            name=name,
            pod_id=pod_id,
            primary_key_column=primary_key_column,
            updated_at=updated_at,
            allowed_actions=allowed_actions,
            visibility=visibility,
        )

        table_detail_response.additional_properties = d
        return table_detail_response

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
