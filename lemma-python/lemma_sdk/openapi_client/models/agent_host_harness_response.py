from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.agent_host_harness_response_capabilities import (
        AgentHostHarnessResponseCapabilities,
    )
    from ..models.agent_host_harness_response_metadata import (
        AgentHostHarnessResponseMetadata,
    )


T = TypeVar("T", bound="AgentHostHarnessResponse")


@_attrs_define
class AgentHostHarnessResponse:
    """
    Attributes:
        adapter_protocol (str):
        adapter_protocol_version (int):
        adapter_version (str):
        auth_state (str):
        capabilities (AgentHostHarnessResponseCapabilities):
        config_options (list[Any]):
        config_revision (str):
        display_name (str):
        fetched_at (datetime.datetime):
        harness_key (str):
        health (str):
        host_id (UUID):
        id (UUID):
        metadata (AgentHostHarnessResponseMetadata):
        stale_after (datetime.datetime):
        stale_reason (None | str):
        upstream_version (None | str):
    """

    adapter_protocol: str
    adapter_protocol_version: int
    adapter_version: str
    auth_state: str
    capabilities: AgentHostHarnessResponseCapabilities
    config_options: list[Any]
    config_revision: str
    display_name: str
    fetched_at: datetime.datetime
    harness_key: str
    health: str
    host_id: UUID
    id: UUID
    metadata: AgentHostHarnessResponseMetadata
    stale_after: datetime.datetime
    stale_reason: None | str
    upstream_version: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        adapter_protocol = self.adapter_protocol

        adapter_protocol_version = self.adapter_protocol_version

        adapter_version = self.adapter_version

        auth_state = self.auth_state

        capabilities = self.capabilities.to_dict()

        config_options = self.config_options

        config_revision = self.config_revision

        display_name = self.display_name

        fetched_at = self.fetched_at.isoformat()

        harness_key = self.harness_key

        health = self.health

        host_id = str(self.host_id)

        id = str(self.id)

        metadata = self.metadata.to_dict()

        stale_after = self.stale_after.isoformat()

        stale_reason: None | str
        stale_reason = self.stale_reason

        upstream_version: None | str
        upstream_version = self.upstream_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "adapter_protocol": adapter_protocol,
                "adapter_protocol_version": adapter_protocol_version,
                "adapter_version": adapter_version,
                "auth_state": auth_state,
                "capabilities": capabilities,
                "config_options": config_options,
                "config_revision": config_revision,
                "display_name": display_name,
                "fetched_at": fetched_at,
                "harness_key": harness_key,
                "health": health,
                "host_id": host_id,
                "id": id,
                "metadata": metadata,
                "stale_after": stale_after,
                "stale_reason": stale_reason,
                "upstream_version": upstream_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_harness_response_capabilities import (
            AgentHostHarnessResponseCapabilities,
        )
        from ..models.agent_host_harness_response_metadata import (
            AgentHostHarnessResponseMetadata,
        )

        d = dict(src_dict)
        adapter_protocol = d.pop("adapter_protocol")

        adapter_protocol_version = d.pop("adapter_protocol_version")

        adapter_version = d.pop("adapter_version")

        auth_state = d.pop("auth_state")

        capabilities = AgentHostHarnessResponseCapabilities.from_dict(
            d.pop("capabilities")
        )

        config_options = cast(list[Any], d.pop("config_options"))

        config_revision = d.pop("config_revision")

        display_name = d.pop("display_name")

        fetched_at = isoparse(d.pop("fetched_at"))

        harness_key = d.pop("harness_key")

        health = d.pop("health")

        host_id = UUID(d.pop("host_id"))

        id = UUID(d.pop("id"))

        metadata = AgentHostHarnessResponseMetadata.from_dict(d.pop("metadata"))

        stale_after = isoparse(d.pop("stale_after"))

        def _parse_stale_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        stale_reason = _parse_stale_reason(d.pop("stale_reason"))

        def _parse_upstream_version(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        upstream_version = _parse_upstream_version(d.pop("upstream_version"))

        agent_host_harness_response = cls(
            adapter_protocol=adapter_protocol,
            adapter_protocol_version=adapter_protocol_version,
            adapter_version=adapter_version,
            auth_state=auth_state,
            capabilities=capabilities,
            config_options=config_options,
            config_revision=config_revision,
            display_name=display_name,
            fetched_at=fetched_at,
            harness_key=harness_key,
            health=health,
            host_id=host_id,
            id=id,
            metadata=metadata,
            stale_after=stale_after,
            stale_reason=stale_reason,
            upstream_version=upstream_version,
        )

        agent_host_harness_response.additional_properties = d
        return agent_host_harness_response

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
