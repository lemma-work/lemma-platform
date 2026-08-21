from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_toolset import AgentToolset
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_resource_permission_response import (
        AgentResourcePermissionResponse,
    )
    from ..models.agent_summary_response_metadata_type_0 import (
        AgentSummaryResponseMetadataType0,
    )


T = TypeVar("T", bound="AgentSummaryResponse")


@_attrs_define
class AgentSummaryResponse:
    """Lean agent shape for list responses.

    Omits the heavy single-resource fields (`instruction`, `input_schema`,
    `output_schema`, `agent_runtime`) — fetch those from `agent.get`. Keeps
    `toolsets` so list cards can show a connection count.

        Attributes:
            created_at (datetime.datetime):
            id (UUID):
            name (str):
            pod_id (UUID):
            updated_at (datetime.datetime):
            user_id (UUID):
            allowed_actions (list[str] | Unset):
            description (None | str | Unset):
            grants (list[AgentResourcePermissionResponse] | None | Unset):
            has_pinned_runtime (bool | Unset):  Default: False.
            icon_url (None | str | Unset):
            metadata (AgentSummaryResponseMetadataType0 | None | Unset):
            takes_input (bool | Unset):  Default: False.
            toolsets (list[AgentToolset] | Unset):
            visibility (str | Unset):  Default: 'POD'.
    """

    created_at: datetime.datetime
    id: UUID
    name: str
    pod_id: UUID
    updated_at: datetime.datetime
    user_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    description: None | str | Unset = UNSET
    grants: list[AgentResourcePermissionResponse] | None | Unset = UNSET
    has_pinned_runtime: bool | Unset = False
    icon_url: None | str | Unset = UNSET
    metadata: AgentSummaryResponseMetadataType0 | None | Unset = UNSET
    takes_input: bool | Unset = False
    toolsets: list[AgentToolset] | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_summary_response_metadata_type_0 import (
            AgentSummaryResponseMetadataType0,
        )

        created_at = self.created_at.isoformat()

        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        grants: list[dict[str, Any]] | None | Unset
        if isinstance(self.grants, Unset):
            grants = UNSET
        elif isinstance(self.grants, list):
            grants = []
            for grants_type_0_item_data in self.grants:
                grants_type_0_item = grants_type_0_item_data.to_dict()
                grants.append(grants_type_0_item)

        else:
            grants = self.grants

        has_pinned_runtime = self.has_pinned_runtime

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, AgentSummaryResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        takes_input = self.takes_input

        toolsets: list[str] | Unset = UNSET
        if not isinstance(self.toolsets, Unset):
            toolsets = []
            for toolsets_item_data in self.toolsets:
                toolsets_item = toolsets_item_data.value
                toolsets.append(toolsets_item)

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "pod_id": pod_id,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if description is not UNSET:
            field_dict["description"] = description
        if grants is not UNSET:
            field_dict["grants"] = grants
        if has_pinned_runtime is not UNSET:
            field_dict["has_pinned_runtime"] = has_pinned_runtime
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if takes_input is not UNSET:
            field_dict["takes_input"] = takes_input
        if toolsets is not UNSET:
            field_dict["toolsets"] = toolsets
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_resource_permission_response import (
            AgentResourcePermissionResponse,
        )
        from ..models.agent_summary_response_metadata_type_0 import (
            AgentSummaryResponseMetadataType0,
        )

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_grants(
            data: object,
        ) -> list[AgentResourcePermissionResponse] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                grants_type_0 = []
                _grants_type_0 = data
                for grants_type_0_item_data in _grants_type_0:
                    grants_type_0_item = AgentResourcePermissionResponse.from_dict(
                        grants_type_0_item_data
                    )

                    grants_type_0.append(grants_type_0_item)

                return grants_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[AgentResourcePermissionResponse] | None | Unset, data)

        grants = _parse_grants(d.pop("grants", UNSET))

        has_pinned_runtime = d.pop("has_pinned_runtime", UNSET)

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        def _parse_metadata(
            data: object,
        ) -> AgentSummaryResponseMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = AgentSummaryResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentSummaryResponseMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        takes_input = d.pop("takes_input", UNSET)

        _toolsets = d.pop("toolsets", UNSET)
        toolsets: list[AgentToolset] | Unset = UNSET
        if _toolsets is not UNSET:
            toolsets = []
            for toolsets_item_data in _toolsets:
                toolsets_item = AgentToolset(toolsets_item_data)

                toolsets.append(toolsets_item)

        visibility = d.pop("visibility", UNSET)

        agent_summary_response = cls(
            created_at=created_at,
            id=id,
            name=name,
            pod_id=pod_id,
            updated_at=updated_at,
            user_id=user_id,
            allowed_actions=allowed_actions,
            description=description,
            grants=grants,
            has_pinned_runtime=has_pinned_runtime,
            icon_url=icon_url,
            metadata=metadata,
            takes_input=takes_input,
            toolsets=toolsets,
            visibility=visibility,
        )

        agent_summary_response.additional_properties = d
        return agent_summary_response

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
