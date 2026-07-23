from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.update_conversation_request_metadata_type_0 import (
        UpdateConversationRequestMetadataType0,
    )


T = TypeVar("T", bound="UpdateConversationRequest")


@_attrs_define
class UpdateConversationRequest:
    """
    Attributes:
        agent_runtime (AgentRuntimeConfig | None | Unset):
        instructions (None | str | Unset):
        metadata (None | Unset | UpdateConversationRequestMetadataType0):
        title (None | str | Unset):
    """

    agent_runtime: AgentRuntimeConfig | None | Unset = UNSET
    instructions: None | str | Unset = UNSET
    metadata: None | Unset | UpdateConversationRequestMetadataType0 = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.update_conversation_request_metadata_type_0 import (
            UpdateConversationRequestMetadataType0,
        )

        agent_runtime: dict[str, Any] | None | Unset
        if isinstance(self.agent_runtime, Unset):
            agent_runtime = UNSET
        elif isinstance(self.agent_runtime, AgentRuntimeConfig):
            agent_runtime = self.agent_runtime.to_dict()
        else:
            agent_runtime = self.agent_runtime

        instructions: None | str | Unset
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, UpdateConversationRequestMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agent_runtime is not UNSET:
            field_dict["agent_runtime"] = agent_runtime
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.update_conversation_request_metadata_type_0 import (
            UpdateConversationRequestMetadataType0,
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

        def _parse_instructions(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_metadata(
            data: object,
        ) -> None | Unset | UpdateConversationRequestMetadataType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = UpdateConversationRequestMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateConversationRequestMetadataType0, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        update_conversation_request = cls(
            agent_runtime=agent_runtime,
            instructions=instructions,
            metadata=metadata,
            title=title,
        )

        update_conversation_request.additional_properties = d
        return update_conversation_request

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
