from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_kind import AgentKind
from ..models.agent_toolset import AgentToolset
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_action_response_input_schema_type_0 import (
        AgentActionResponseInputSchemaType0,
    )
    from ..models.agent_action_response_metadata_type_0 import (
        AgentActionResponseMetadataType0,
    )
    from ..models.agent_action_response_output_schema_type_0 import (
        AgentActionResponseOutputSchemaType0,
    )
    from ..models.agent_runtime_config import AgentRuntimeConfig


T = TypeVar("T", bound="AgentActionResponse")


@_attrs_define
class AgentActionResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        instruction (str):
        name (str):
        pod_id (UUID):
        updated_at (datetime.datetime):
        user_id (UUID):
        agent_runtime (AgentRuntimeConfig | None | Unset):
        allowed_actions (list[str] | Unset):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        input_schema (AgentActionResponseInputSchemaType0 | None | Unset):
        kind (AgentKind | Unset): Whether somebody made this agent, or the pod came with it.

            The pod's default assistant used to be the absence of an agent: a
            conversation naming nobody, synthesised at runtime against one sentinel id
            shared by every pod. That absence could not be pointed at by a foreign key,
            so anything wanting to name it grew its own way of saying so — a boolean on
            the schedule, a second boolean on a channel route, a magic string in a map
            of who answers whose DMs.

            A kind is one way of saying it, in the row itself. ``POD_DEFAULT`` is
            pinned by check constraints to exactly one row per pod, whose id is the
            pod's own — so "is this the default assistant?" stays a comparison rather
            than a query, which matters on paths that answer it per request.
        metadata (AgentActionResponseMetadataType0 | None | Unset):
        output_schema (AgentActionResponseOutputSchemaType0 | None | Unset):
        toolsets (list[AgentToolset] | Unset):
        visibility (str | Unset):  Default: 'POD'.
    """

    created_at: datetime.datetime
    id: UUID
    instruction: str
    name: str
    pod_id: UUID
    updated_at: datetime.datetime
    user_id: UUID
    agent_runtime: AgentRuntimeConfig | None | Unset = UNSET
    allowed_actions: list[str] | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    input_schema: AgentActionResponseInputSchemaType0 | None | Unset = UNSET
    kind: AgentKind | Unset = UNSET
    metadata: AgentActionResponseMetadataType0 | None | Unset = UNSET
    output_schema: AgentActionResponseOutputSchemaType0 | None | Unset = UNSET
    toolsets: list[AgentToolset] | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_action_response_input_schema_type_0 import (
            AgentActionResponseInputSchemaType0,
        )
        from ..models.agent_action_response_metadata_type_0 import (
            AgentActionResponseMetadataType0,
        )
        from ..models.agent_action_response_output_schema_type_0 import (
            AgentActionResponseOutputSchemaType0,
        )
        from ..models.agent_runtime_config import AgentRuntimeConfig

        created_at = self.created_at.isoformat()

        id = str(self.id)

        instruction = self.instruction

        name = self.name

        pod_id = str(self.pod_id)

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        agent_runtime: dict[str, Any] | None | Unset
        if isinstance(self.agent_runtime, Unset):
            agent_runtime = UNSET
        elif isinstance(self.agent_runtime, AgentRuntimeConfig):
            agent_runtime = self.agent_runtime.to_dict()
        else:
            agent_runtime = self.agent_runtime

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

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

        input_schema: dict[str, Any] | None | Unset
        if isinstance(self.input_schema, Unset):
            input_schema = UNSET
        elif isinstance(self.input_schema, AgentActionResponseInputSchemaType0):
            input_schema = self.input_schema.to_dict()
        else:
            input_schema = self.input_schema

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, AgentActionResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, AgentActionResponseOutputSchemaType0):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

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
                "instruction": instruction,
                "name": name,
                "pod_id": pod_id,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if agent_runtime is not UNSET:
            field_dict["agent_runtime"] = agent_runtime
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if input_schema is not UNSET:
            field_dict["input_schema"] = input_schema
        if kind is not UNSET:
            field_dict["kind"] = kind
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema
        if toolsets is not UNSET:
            field_dict["toolsets"] = toolsets
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_action_response_input_schema_type_0 import (
            AgentActionResponseInputSchemaType0,
        )
        from ..models.agent_action_response_metadata_type_0 import (
            AgentActionResponseMetadataType0,
        )
        from ..models.agent_action_response_output_schema_type_0 import (
            AgentActionResponseOutputSchemaType0,
        )
        from ..models.agent_runtime_config import AgentRuntimeConfig

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        instruction = d.pop("instruction")

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        def _parse_agent_runtime(data: object) -> AgentRuntimeConfig | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                agent_runtime_type_0 = AgentRuntimeConfig.from_dict(data)

                return agent_runtime_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentRuntimeConfig | None | Unset, data)

        agent_runtime = _parse_agent_runtime(d.pop("agent_runtime", UNSET))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

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

        def _parse_input_schema(
            data: object,
        ) -> AgentActionResponseInputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_schema_type_0 = AgentActionResponseInputSchemaType0.from_dict(
                    data
                )

                return input_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentActionResponseInputSchemaType0 | None | Unset, data)

        input_schema = _parse_input_schema(d.pop("input_schema", UNSET))

        _kind = d.pop("kind", UNSET)
        kind: AgentKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = AgentKind(_kind)

        def _parse_metadata(
            data: object,
        ) -> AgentActionResponseMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = AgentActionResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentActionResponseMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_output_schema(
            data: object,
        ) -> AgentActionResponseOutputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = AgentActionResponseOutputSchemaType0.from_dict(
                    data
                )

                return output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentActionResponseOutputSchemaType0 | None | Unset, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        _toolsets = d.pop("toolsets", UNSET)
        toolsets: list[AgentToolset] | Unset = UNSET
        if _toolsets is not UNSET:
            toolsets = []
            for toolsets_item_data in _toolsets:
                toolsets_item = AgentToolset(toolsets_item_data)

                toolsets.append(toolsets_item)

        visibility = d.pop("visibility", UNSET)

        agent_action_response = cls(
            created_at=created_at,
            id=id,
            instruction=instruction,
            name=name,
            pod_id=pod_id,
            updated_at=updated_at,
            user_id=user_id,
            agent_runtime=agent_runtime,
            allowed_actions=allowed_actions,
            description=description,
            icon_url=icon_url,
            input_schema=input_schema,
            kind=kind,
            metadata=metadata,
            output_schema=output_schema,
            toolsets=toolsets,
            visibility=visibility,
        )

        agent_action_response.additional_properties = d
        return agent_action_response

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
