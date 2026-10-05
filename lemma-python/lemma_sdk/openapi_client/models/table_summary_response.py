from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="TableSummaryResponse")


@_attrs_define
class TableSummaryResponse:
    """Lean table shape for list responses.

    Omits the full `columns` definitions and `config` — fetch those from
    `table.get`. Exposes a cheap `column_count` for list views.

        Attributes:
            created_at (datetime.datetime):
            enable_rls (bool):
            id (UUID):
            name (str):
            pod_id (UUID):
            primary_key_column (str):
            updated_at (datetime.datetime):
            allowed_actions (list[str] | Unset):
            column_count (int | Unset):  Default: 0.
            contact_owned (bool | Unset):  Default: False.
            visibility (str | Unset):  Default: 'POD'.
    """

    created_at: datetime.datetime
    enable_rls: bool
    id: UUID
    name: str
    pod_id: UUID
    primary_key_column: str
    updated_at: datetime.datetime
    allowed_actions: list[str] | Unset = UNSET
    column_count: int | Unset = 0
    contact_owned: bool | Unset = False
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
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

        column_count = self.column_count

        contact_owned = self.contact_owned

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
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
        if column_count is not UNSET:
            field_dict["column_count"] = column_count
        if contact_owned is not UNSET:
            field_dict["contact_owned"] = contact_owned
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        enable_rls = d.pop("enable_rls")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        primary_key_column = d.pop("primary_key_column")

        updated_at = isoparse(d.pop("updated_at"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        column_count = d.pop("column_count", UNSET)

        contact_owned = d.pop("contact_owned", UNSET)

        visibility = d.pop("visibility", UNSET)

        table_summary_response = cls(
            created_at=created_at,
            enable_rls=enable_rls,
            id=id,
            name=name,
            pod_id=pod_id,
            primary_key_column=primary_key_column,
            updated_at=updated_at,
            allowed_actions=allowed_actions,
            column_count=column_count,
            contact_owned=contact_owned,
            visibility=visibility,
        )

        table_summary_response.additional_properties = d
        return table_summary_response

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
