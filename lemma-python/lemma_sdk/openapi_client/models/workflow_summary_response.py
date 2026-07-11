from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workflow_mode import WorkflowMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkflowSummaryResponse")


@_attrs_define
class WorkflowSummaryResponse:
    """Lean workflow shape for list responses.

    Omits the full graph (`nodes`/`edges`/`start`) — fetch those from
    `workflow.get`. Carries cheap derived `node_count`/`node_types` so list
    views can show step counts and participant badges without the graph.

        Attributes:
            id (UUID):
            name (str):
            pod_id (UUID):
            allowed_actions (list[str] | Unset):
            created_at (datetime.datetime | None | Unset):
            description (None | str | Unset):
            icon_url (None | str | Unset):
            is_active (bool | Unset):  Default: True.
            mode (WorkflowMode | Unset): Workflow schedule ownership mode.
            node_count (int | Unset):  Default: 0.
            node_types (list[str] | Unset):
            updated_at (datetime.datetime | None | Unset):
            visibility (str | Unset):  Default: 'POD'.
    """

    id: UUID
    name: str
    pod_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    is_active: bool | Unset = True
    mode: WorkflowMode | Unset = UNSET
    node_count: int | Unset = 0
    node_types: list[str] | Unset = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        is_active = self.is_active

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        node_count = self.node_count

        node_types: list[str] | Unset = UNSET
        if not isinstance(self.node_types, Unset):
            node_types = self.node_types

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "pod_id": pod_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if is_active is not UNSET:
            field_dict["is_active"] = is_active
        if mode is not UNSET:
            field_dict["mode"] = mode
        if node_count is not UNSET:
            field_dict["node_count"] = node_count
        if node_types is not UNSET:
            field_dict["node_types"] = node_types
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        is_active = d.pop("is_active", UNSET)

        _mode = d.pop("mode", UNSET)
        mode: WorkflowMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = WorkflowMode(_mode)

        node_count = d.pop("node_count", UNSET)

        node_types = cast(list[str], d.pop("node_types", UNSET))

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = isoparse(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        visibility = d.pop("visibility", UNSET)

        workflow_summary_response = cls(
            id=id,
            name=name,
            pod_id=pod_id,
            allowed_actions=allowed_actions,
            created_at=created_at,
            description=description,
            icon_url=icon_url,
            is_active=is_active,
            mode=mode,
            node_count=node_count,
            node_types=node_types,
            updated_at=updated_at,
            visibility=visibility,
        )

        workflow_summary_response.additional_properties = d
        return workflow_summary_response

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
