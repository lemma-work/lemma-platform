from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_toolset import AgentToolset
from ..models.resource_visibility import ResourceVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_permissions_replace_request import (
        AgentPermissionsReplaceRequest,
    )
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.create_agent_request_input_schema_type_0 import (
        CreateAgentRequestInputSchemaType0,
    )
    from ..models.create_agent_request_metadata_type_0 import (
        CreateAgentRequestMetadataType0,
    )
    from ..models.create_agent_request_output_schema_type_0 import (
        CreateAgentRequestOutputSchemaType0,
    )


T = TypeVar("T", bound="CreateAgentRequest")


@_attrs_define
class CreateAgentRequest:
    """
    Attributes:
        instruction (str):
        name (str):
        agent_runtime (AgentRuntimeConfig | None | Unset):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        input_schema (CreateAgentRequestInputSchemaType0 | None | Unset):
        metadata (CreateAgentRequestMetadataType0 | None | Unset):
        output_schema (CreateAgentRequestOutputSchemaType0 | None | Unset):
        permissions (AgentPermissionsReplaceRequest | None | Unset): Optional resource grants to apply to the new agent
            in the same request. Equivalent to calling the permissions-replace endpoint right after create — grants are
            keyed by resource_name.
        toolsets (list[AgentToolset] | Unset): Toolsets the agent declares. Omit the field to start with web search and
            memory; pass an explicit empty list for none.
        visibility (ResourceVisibility | Unset):
    """

    instruction: str
    name: str
    agent_runtime: AgentRuntimeConfig | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    input_schema: CreateAgentRequestInputSchemaType0 | None | Unset = UNSET
    metadata: CreateAgentRequestMetadataType0 | None | Unset = UNSET
    output_schema: CreateAgentRequestOutputSchemaType0 | None | Unset = UNSET
    permissions: AgentPermissionsReplaceRequest | None | Unset = UNSET
    toolsets: list[AgentToolset] | Unset = UNSET
    visibility: ResourceVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_permissions_replace_request import (
            AgentPermissionsReplaceRequest,
        )
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.create_agent_request_input_schema_type_0 import (
            CreateAgentRequestInputSchemaType0,
        )
        from ..models.create_agent_request_metadata_type_0 import (
            CreateAgentRequestMetadataType0,
        )
        from ..models.create_agent_request_output_schema_type_0 import (
            CreateAgentRequestOutputSchemaType0,
        )

        instruction = self.instruction

        name = self.name

        agent_runtime: dict[str, Any] | None | Unset
        if isinstance(self.agent_runtime, Unset):
            agent_runtime = UNSET
        elif isinstance(self.agent_runtime, AgentRuntimeConfig):
            agent_runtime = self.agent_runtime.to_dict()
        else:
            agent_runtime = self.agent_runtime

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
        elif isinstance(self.input_schema, CreateAgentRequestInputSchemaType0):
            input_schema = self.input_schema.to_dict()
        else:
            input_schema = self.input_schema

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, CreateAgentRequestMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, CreateAgentRequestOutputSchemaType0):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

        permissions: dict[str, Any] | None | Unset
        if isinstance(self.permissions, Unset):
            permissions = UNSET
        elif isinstance(self.permissions, AgentPermissionsReplaceRequest):
            permissions = self.permissions.to_dict()
        else:
            permissions = self.permissions

        toolsets: list[str] | Unset = UNSET
        if not isinstance(self.toolsets, Unset):
            toolsets = []
            for toolsets_item_data in self.toolsets:
                toolsets_item = toolsets_item_data.value
                toolsets.append(toolsets_item)

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "instruction": instruction,
                "name": name,
            }
        )
        if agent_runtime is not UNSET:
            field_dict["agent_runtime"] = agent_runtime
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if input_schema is not UNSET:
            field_dict["input_schema"] = input_schema
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema
        if permissions is not UNSET:
            field_dict["permissions"] = permissions
        if toolsets is not UNSET:
            field_dict["toolsets"] = toolsets
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_permissions_replace_request import (
            AgentPermissionsReplaceRequest,
        )
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.create_agent_request_input_schema_type_0 import (
            CreateAgentRequestInputSchemaType0,
        )
        from ..models.create_agent_request_metadata_type_0 import (
            CreateAgentRequestMetadataType0,
        )
        from ..models.create_agent_request_output_schema_type_0 import (
            CreateAgentRequestOutputSchemaType0,
        )

        d = dict(src_dict)
        instruction = d.pop("instruction")

        name = d.pop("name")

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
        ) -> CreateAgentRequestInputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_schema_type_0 = CreateAgentRequestInputSchemaType0.from_dict(data)

                return input_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateAgentRequestInputSchemaType0 | None | Unset, data)

        input_schema = _parse_input_schema(d.pop("input_schema", UNSET))

        def _parse_metadata(
            data: object,
        ) -> CreateAgentRequestMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = CreateAgentRequestMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateAgentRequestMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_output_schema(
            data: object,
        ) -> CreateAgentRequestOutputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = CreateAgentRequestOutputSchemaType0.from_dict(
                    data
                )

                return output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateAgentRequestOutputSchemaType0 | None | Unset, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        def _parse_permissions(
            data: object,
        ) -> AgentPermissionsReplaceRequest | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                permissions_type_0 = AgentPermissionsReplaceRequest.from_dict(data)

                return permissions_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentPermissionsReplaceRequest | None | Unset, data)

        permissions = _parse_permissions(d.pop("permissions", UNSET))

        _toolsets = d.pop("toolsets", UNSET)
        toolsets: list[AgentToolset] | Unset = UNSET
        if _toolsets is not UNSET:
            toolsets = []
            for toolsets_item_data in _toolsets:
                toolsets_item = AgentToolset(toolsets_item_data)

                toolsets.append(toolsets_item)

        _visibility = d.pop("visibility", UNSET)
        visibility: ResourceVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = ResourceVisibility(_visibility)

        create_agent_request = cls(
            instruction=instruction,
            name=name,
            agent_runtime=agent_runtime,
            description=description,
            icon_url=icon_url,
            input_schema=input_schema,
            metadata=metadata,
            output_schema=output_schema,
            permissions=permissions,
            toolsets=toolsets,
            visibility=visibility,
        )

        create_agent_request.additional_properties = d
        return create_agent_request

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
