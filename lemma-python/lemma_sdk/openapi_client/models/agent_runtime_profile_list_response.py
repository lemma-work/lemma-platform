from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.anthropic_compatible_runtime_profile_response import (
        AnthropicCompatibleRuntimeProfileResponse,
    )
    from ..models.azure_open_ai_runtime_profile_response import (
        AzureOpenAIRuntimeProfileResponse,
    )
    from ..models.google_vertex_runtime_profile_response import (
        GoogleVertexRuntimeProfileResponse,
    )
    from ..models.harness_runtime_profile_response import HarnessRuntimeProfileResponse
    from ..models.open_ai_compatible_runtime_profile_response import (
        OpenAICompatibleRuntimeProfileResponse,
    )


T = TypeVar("T", bound="AgentRuntimeProfileListResponse")


@_attrs_define
class AgentRuntimeProfileListResponse:
    """
    Attributes:
        default_runtime (AgentRuntimeConfig): Select an agent runtime profile and optional catalog model.
        items (list[AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse |
            GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse | OpenAICompatibleRuntimeProfileResponse]):
    """

    default_runtime: AgentRuntimeConfig
    items: list[
        AnthropicCompatibleRuntimeProfileResponse
        | AzureOpenAIRuntimeProfileResponse
        | GoogleVertexRuntimeProfileResponse
        | HarnessRuntimeProfileResponse
        | OpenAICompatibleRuntimeProfileResponse
    ]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.anthropic_compatible_runtime_profile_response import (
            AnthropicCompatibleRuntimeProfileResponse,
        )
        from ..models.azure_open_ai_runtime_profile_response import (
            AzureOpenAIRuntimeProfileResponse,
        )
        from ..models.google_vertex_runtime_profile_response import (
            GoogleVertexRuntimeProfileResponse,
        )
        from ..models.open_ai_compatible_runtime_profile_response import (
            OpenAICompatibleRuntimeProfileResponse,
        )

        default_runtime = self.default_runtime.to_dict()

        items = []
        for items_item_data in self.items:
            items_item: dict[str, Any]
            if isinstance(items_item_data, OpenAICompatibleRuntimeProfileResponse):
                items_item = items_item_data.to_dict()
            elif isinstance(items_item_data, AnthropicCompatibleRuntimeProfileResponse):
                items_item = items_item_data.to_dict()
            elif isinstance(items_item_data, AzureOpenAIRuntimeProfileResponse):
                items_item = items_item_data.to_dict()
            elif isinstance(items_item_data, GoogleVertexRuntimeProfileResponse):
                items_item = items_item_data.to_dict()
            else:
                items_item = items_item_data.to_dict()

            items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "default_runtime": default_runtime,
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.anthropic_compatible_runtime_profile_response import (
            AnthropicCompatibleRuntimeProfileResponse,
        )
        from ..models.azure_open_ai_runtime_profile_response import (
            AzureOpenAIRuntimeProfileResponse,
        )
        from ..models.google_vertex_runtime_profile_response import (
            GoogleVertexRuntimeProfileResponse,
        )
        from ..models.harness_runtime_profile_response import (
            HarnessRuntimeProfileResponse,
        )
        from ..models.open_ai_compatible_runtime_profile_response import (
            OpenAICompatibleRuntimeProfileResponse,
        )

        d = dict(src_dict)
        default_runtime = AgentRuntimeConfig.from_dict(d.pop("default_runtime"))

        items = []
        _items = d.pop("items")
        for items_item_data in _items:

            def _parse_items_item(
                data: object,
            ) -> (
                AnthropicCompatibleRuntimeProfileResponse
                | AzureOpenAIRuntimeProfileResponse
                | GoogleVertexRuntimeProfileResponse
                | HarnessRuntimeProfileResponse
                | OpenAICompatibleRuntimeProfileResponse
            ):
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    items_item_type_0 = (
                        OpenAICompatibleRuntimeProfileResponse.from_dict(data)
                    )

                    return items_item_type_0
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    items_item_type_1 = (
                        AnthropicCompatibleRuntimeProfileResponse.from_dict(data)
                    )

                    return items_item_type_1
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    items_item_type_2 = AzureOpenAIRuntimeProfileResponse.from_dict(
                        data
                    )

                    return items_item_type_2
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    items_item_type_3 = GoogleVertexRuntimeProfileResponse.from_dict(
                        data
                    )

                    return items_item_type_3
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                if not isinstance(data, dict):
                    raise TypeError()
                items_item_type_4 = HarnessRuntimeProfileResponse.from_dict(data)

                return items_item_type_4

            items_item = _parse_items_item(items_item_data)

            items.append(items_item)

        agent_runtime_profile_list_response = cls(
            default_runtime=default_runtime,
            items=items,
        )

        agent_runtime_profile_list_response.additional_properties = d
        return agent_runtime_profile_list_response

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
