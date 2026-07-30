from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_host_harness_health import AgentHostHarnessHealth
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_config_option import AgentHostConfigOption
    from ..models.agent_host_harness_capabilities import AgentHostHarnessCapabilities


T = TypeVar("T", bound="AgentHostHarnessSnapshot")


@_attrs_define
class AgentHostHarnessSnapshot:
    """
    Attributes:
        adapter_version (str):
        config_revision (str):
        display_name (str):
        harness_key (str):
        health (AgentHostHarnessHealth):
        stale_after (datetime.datetime):
        capabilities (AgentHostHarnessCapabilities | Unset):
        config_options (list[AgentHostConfigOption] | Unset):
        stale_reason (None | str | Unset):
        upstream_version (None | str | Unset):
    """

    adapter_version: str
    config_revision: str
    display_name: str
    harness_key: str
    health: AgentHostHarnessHealth
    stale_after: datetime.datetime
    capabilities: AgentHostHarnessCapabilities | Unset = UNSET
    config_options: list[AgentHostConfigOption] | Unset = UNSET
    stale_reason: None | str | Unset = UNSET
    upstream_version: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        adapter_version = self.adapter_version

        config_revision = self.config_revision

        display_name = self.display_name

        harness_key = self.harness_key

        health = self.health.value

        stale_after = self.stale_after.isoformat()

        capabilities: dict[str, Any] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = self.capabilities.to_dict()

        config_options: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.config_options, Unset):
            config_options = []
            for config_options_item_data in self.config_options:
                config_options_item = config_options_item_data.to_dict()
                config_options.append(config_options_item)

        stale_reason: None | str | Unset
        if isinstance(self.stale_reason, Unset):
            stale_reason = UNSET
        else:
            stale_reason = self.stale_reason

        upstream_version: None | str | Unset
        if isinstance(self.upstream_version, Unset):
            upstream_version = UNSET
        else:
            upstream_version = self.upstream_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "adapter_version": adapter_version,
                "config_revision": config_revision,
                "display_name": display_name,
                "harness_key": harness_key,
                "health": health,
                "stale_after": stale_after,
            }
        )
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if config_options is not UNSET:
            field_dict["config_options"] = config_options
        if stale_reason is not UNSET:
            field_dict["stale_reason"] = stale_reason
        if upstream_version is not UNSET:
            field_dict["upstream_version"] = upstream_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_config_option import AgentHostConfigOption
        from ..models.agent_host_harness_capabilities import (
            AgentHostHarnessCapabilities,
        )

        d = dict(src_dict)
        adapter_version = d.pop("adapter_version")

        config_revision = d.pop("config_revision")

        display_name = d.pop("display_name")

        harness_key = d.pop("harness_key")

        health = AgentHostHarnessHealth(d.pop("health"))

        stale_after = isoparse(d.pop("stale_after"))

        _capabilities = d.pop("capabilities", UNSET)
        capabilities: AgentHostHarnessCapabilities | Unset
        if isinstance(_capabilities, Unset):
            capabilities = UNSET
        else:
            capabilities = AgentHostHarnessCapabilities.from_dict(_capabilities)

        _config_options = d.pop("config_options", UNSET)
        config_options: list[AgentHostConfigOption] | Unset = UNSET
        if _config_options is not UNSET:
            config_options = []
            for config_options_item_data in _config_options:
                config_options_item = AgentHostConfigOption.from_dict(
                    config_options_item_data
                )

                config_options.append(config_options_item)

        def _parse_stale_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        stale_reason = _parse_stale_reason(d.pop("stale_reason", UNSET))

        def _parse_upstream_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        upstream_version = _parse_upstream_version(d.pop("upstream_version", UNSET))

        agent_host_harness_snapshot = cls(
            adapter_version=adapter_version,
            config_revision=config_revision,
            display_name=display_name,
            harness_key=harness_key,
            health=health,
            stale_after=stale_after,
            capabilities=capabilities,
            config_options=config_options,
            stale_reason=stale_reason,
            upstream_version=upstream_version,
        )

        agent_host_harness_snapshot.additional_properties = d
        return agent_host_harness_snapshot

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
