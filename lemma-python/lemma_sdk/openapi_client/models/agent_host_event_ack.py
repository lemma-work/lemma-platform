from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AgentHostEventAck")


@_attrs_define
class AgentHostEventAck:
    """
    Attributes:
        acked_through (int):
        lease_epoch (int):
        run_id (UUID):
    """

    acked_through: int
    lease_epoch: int
    run_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        acked_through = self.acked_through

        lease_epoch = self.lease_epoch

        run_id = str(self.run_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "acked_through": acked_through,
                "lease_epoch": lease_epoch,
                "run_id": run_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        acked_through = d.pop("acked_through")

        lease_epoch = d.pop("lease_epoch")

        run_id = UUID(d.pop("run_id"))

        agent_host_event_ack = cls(
            acked_through=acked_through,
            lease_epoch=lease_epoch,
            run_id=run_id,
        )

        agent_host_event_ack.additional_properties = d
        return agent_host_event_ack

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
