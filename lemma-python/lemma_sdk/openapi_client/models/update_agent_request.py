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
    from ..models.update_agent_request_input_schema_type_0 import (
        UpdateAgentRequestInputSchemaType0,
    )
    from ..models.update_agent_request_metadata_type_0 import (
        UpdateAgentRequestMetadataType0,
    )
    from ..models.update_agent_request_output_schema_type_0 import (
        UpdateAgentRequestOutputSchemaType0,
    )


T = TypeVar("T", bound="UpdateAgentRequest")


@_attrs_define
class UpdateAgentRequest:
    """
    Attributes:
        agent_runtime (AgentRuntimeConfig | None | Unset):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        input_schema (None | Unset | UpdateAgentRequestInputSchemaType0):
        instruction (None | str | Unset):
        metadata (None | Unset | UpdateAgentRequestMetadataType0):
        output_schema (None | Unset | UpdateAgentRequestOutputSchemaType0):
        permissions (AgentPermissionsReplaceRequest | None | Unset): Optional resource grants to REPLACE on this agent,
            in the same request. Equivalent to calling the permissions-replace endpoint right after update — grants are
            keyed by resource_name. Omit the key to leave existing grants alone; an empty grant list revokes them.
        toolsets (list[AgentToolset] | None | Unset):
        visibility (None | ResourceVisibility | Unset):
    """

    agent_runtime: AgentRuntimeConfig | None | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    input_schema: None | Unset | UpdateAgentRequestInputSchemaType0 = UNSET
    instruction: None | str | Unset = UNSET
    metadata: None | Unset | UpdateAgentRequestMetadataType0 = UNSET
    output_schema: None | Unset | UpdateAgentRequestOutputSchemaType0 = UNSET
    permissions: AgentPermissionsReplaceRequest | None | Unset = UNSET
    toolsets: list[AgentToolset] | None | Unset = UNSET
    visibility: None | ResourceVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_permissions_replace_request import (
            AgentPermissionsReplaceRequest,
        )
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.update_agent_request_input_schema_type_0 import (
            UpdateAgentRequestInputSchemaType0,
        )
        from ..models.update_agent_request_metadata_type_0 import (
            UpdateAgentRequestMetadataType0,
        )
        from ..models.update_agent_request_output_schema_type_0 import (
            UpdateAgentRequestOutputSchemaType0,
        )

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
        elif isinstance(self.input_schema, UpdateAgentRequestInputSchemaType0):
            input_schema = self.input_schema.to_dict()
        else:
            input_schema = self.input_schema

        instruction: None | str | Unset
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, UpdateAgentRequestMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, UpdateAgentRequestOutputSchemaType0):
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

        toolsets: list[str] | None | Unset
        if isinstance(self.toolsets, Unset):
            toolsets = UNSET
        elif isinstance(self.toolsets, list):
            toolsets = []
            for toolsets_type_0_item_data in self.toolsets:
                toolsets_type_0_item = toolsets_type_0_item_data.value
                toolsets.append(toolsets_type_0_item)

        else:
            toolsets = self.toolsets

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, ResourceVisibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agent_runtime is not UNSET:
            field_dict["agent_runtime"] = agent_runtime
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if input_schema is not UNSET:
            field_dict["input_schema"] = input_schema
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
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
        from ..models.update_agent_request_input_schema_type_0 import (
            UpdateAgentRequestInputSchemaType0,
        )
        from ..models.update_agent_request_metadata_type_0 import (
            UpdateAgentRequestMetadataType0,
        )
        from ..models.update_agent_request_output_schema_type_0 import (
            UpdateAgentRequestOutputSchemaType0,
        )

        d = dict(src_dict)

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
        ) -> None | Unset | UpdateAgentRequestInputSchemaType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_schema_type_0 = UpdateAgentRequestInputSchemaType0.from_dict(data)

                return input_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateAgentRequestInputSchemaType0, data)

        input_schema = _parse_input_schema(d.pop("input_schema", UNSET))

        def _parse_instruction(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instruction = _parse_instruction(d.pop("instruction", UNSET))

        def _parse_metadata(
            data: object,
        ) -> None | Unset | UpdateAgentRequestMetadataType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = UpdateAgentRequestMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateAgentRequestMetadataType0, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_output_schema(
            data: object,
        ) -> None | Unset | UpdateAgentRequestOutputSchemaType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = UpdateAgentRequestOutputSchemaType0.from_dict(
                    data
                )

                return output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateAgentRequestOutputSchemaType0, data)

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

        def _parse_toolsets(data: object) -> list[AgentToolset] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                toolsets_type_0 = []
                _toolsets_type_0 = data
                for toolsets_type_0_item_data in _toolsets_type_0:
                    toolsets_type_0_item = AgentToolset(toolsets_type_0_item_data)

                    toolsets_type_0.append(toolsets_type_0_item)

                return toolsets_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[AgentToolset] | None | Unset, data)

        toolsets = _parse_toolsets(d.pop("toolsets", UNSET))

        def _parse_visibility(data: object) -> None | ResourceVisibility | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = ResourceVisibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ResourceVisibility | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update_agent_request = cls(
            agent_runtime=agent_runtime,
            description=description,
            icon_url=icon_url,
            input_schema=input_schema,
            instruction=instruction,
            metadata=metadata,
            output_schema=output_schema,
            permissions=permissions,
            toolsets=toolsets,
            visibility=visibility,
        )

        update_agent_request.additional_properties = d
        return update_agent_request

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
