from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_capacity import AgentHostCapacity
    from ..models.agent_host_run_checkpoint import AgentHostRunCheckpoint
    from ..models.host_hello import HostHello


T = TypeVar("T", bound="AgentHostPollRequest")


@_attrs_define
class AgentHostPollRequest:
    """
    Attributes:
        hello (HostHello):
        acknowledged_command_ids (list[UUID] | Unset):
        capacity (AgentHostCapacity | Unset):
        checkpoints (list[AgentHostRunCheckpoint] | Unset):
    """

    hello: HostHello
    acknowledged_command_ids: list[UUID] | Unset = UNSET
    capacity: AgentHostCapacity | Unset = UNSET
    checkpoints: list[AgentHostRunCheckpoint] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        hello = self.hello.to_dict()

        acknowledged_command_ids: list[str] | Unset = UNSET
        if not isinstance(self.acknowledged_command_ids, Unset):
            acknowledged_command_ids = []
            for acknowledged_command_ids_item_data in self.acknowledged_command_ids:
                acknowledged_command_ids_item = str(acknowledged_command_ids_item_data)
                acknowledged_command_ids.append(acknowledged_command_ids_item)

        capacity: dict[str, Any] | Unset = UNSET
        if not isinstance(self.capacity, Unset):
            capacity = self.capacity.to_dict()

        checkpoints: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.checkpoints, Unset):
            checkpoints = []
            for checkpoints_item_data in self.checkpoints:
                checkpoints_item = checkpoints_item_data.to_dict()
                checkpoints.append(checkpoints_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "hello": hello,
            }
        )
        if acknowledged_command_ids is not UNSET:
            field_dict["acknowledged_command_ids"] = acknowledged_command_ids
        if capacity is not UNSET:
            field_dict["capacity"] = capacity
        if checkpoints is not UNSET:
            field_dict["checkpoints"] = checkpoints

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_capacity import AgentHostCapacity
        from ..models.agent_host_run_checkpoint import AgentHostRunCheckpoint
        from ..models.host_hello import HostHello

        d = dict(src_dict)
        hello = HostHello.from_dict(d.pop("hello"))

        _acknowledged_command_ids = d.pop("acknowledged_command_ids", UNSET)
        acknowledged_command_ids: list[UUID] | Unset = UNSET
        if _acknowledged_command_ids is not UNSET:
            acknowledged_command_ids = []
            for acknowledged_command_ids_item_data in _acknowledged_command_ids:
                acknowledged_command_ids_item = UUID(acknowledged_command_ids_item_data)

                acknowledged_command_ids.append(acknowledged_command_ids_item)

        _capacity = d.pop("capacity", UNSET)
        capacity: AgentHostCapacity | Unset
        if isinstance(_capacity, Unset):
            capacity = UNSET
        else:
            capacity = AgentHostCapacity.from_dict(_capacity)

        _checkpoints = d.pop("checkpoints", UNSET)
        checkpoints: list[AgentHostRunCheckpoint] | Unset = UNSET
        if _checkpoints is not UNSET:
            checkpoints = []
            for checkpoints_item_data in _checkpoints:
                checkpoints_item = AgentHostRunCheckpoint.from_dict(
                    checkpoints_item_data
                )

                checkpoints.append(checkpoints_item)

        agent_host_poll_request = cls(
            hello=hello,
            acknowledged_command_ids=acknowledged_command_ids,
            capacity=capacity,
            checkpoints=checkpoints,
        )

        agent_host_poll_request.additional_properties = d
        return agent_host_poll_request

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
