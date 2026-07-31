from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_host_status import AgentHostStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_command import AgentHostCommand


T = TypeVar("T", bound="AgentHostPollResponse")


@_attrs_define
class AgentHostPollResponse:
    """
    Attributes:
        host_status (AgentHostStatus):
        commands (list[AgentHostCommand] | Unset):
        poll_after_ms (int | Unset):  Default: 0.
        protocol_version (int | Unset):  Default: 2.
    """

    host_status: AgentHostStatus
    commands: list[AgentHostCommand] | Unset = UNSET
    poll_after_ms: int | Unset = 0
    protocol_version: int | Unset = 2
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_status = self.host_status.value

        commands: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.commands, Unset):
            commands = []
            for commands_item_data in self.commands:
                commands_item = commands_item_data.to_dict()
                commands.append(commands_item)

        poll_after_ms = self.poll_after_ms

        protocol_version = self.protocol_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_status": host_status,
            }
        )
        if commands is not UNSET:
            field_dict["commands"] = commands
        if poll_after_ms is not UNSET:
            field_dict["poll_after_ms"] = poll_after_ms
        if protocol_version is not UNSET:
            field_dict["protocol_version"] = protocol_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_command import AgentHostCommand

        d = dict(src_dict)
        host_status = AgentHostStatus(d.pop("host_status"))

        _commands = d.pop("commands", UNSET)
        commands: list[AgentHostCommand] | Unset = UNSET
        if _commands is not UNSET:
            commands = []
            for commands_item_data in _commands:
                commands_item = AgentHostCommand.from_dict(commands_item_data)

                commands.append(commands_item)

        poll_after_ms = d.pop("poll_after_ms", UNSET)

        protocol_version = d.pop("protocol_version", UNSET)

        agent_host_poll_response = cls(
            host_status=host_status,
            commands=commands,
            poll_after_ms=poll_after_ms,
            protocol_version=protocol_version,
        )

        agent_host_poll_response.additional_properties = d
        return agent_host_poll_response

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
