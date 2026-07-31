from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_host_event_type import AgentHostEventType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_event_payload import AgentHostEventPayload


T = TypeVar("T", bound="AgentHostEvent")


@_attrs_define
class AgentHostEvent:
    """One run event on its way to the run's Redis Stream.

    There is no event id: events are deduplicated by ``sequence`` against the
    stream's watermark, which is what a resend after a Redis flush relies on.
    There is no host timestamp either -- a Redis stream id already embeds the
    millisecond it was appended.

        Attributes:
            lease_epoch (int):
            run_id (UUID):
            sequence (int):
            type_ (AgentHostEventType):
            object_id (None | str | Unset):
            payload (AgentHostEventPayload | Unset):
    """

    lease_epoch: int
    run_id: UUID
    sequence: int
    type_: AgentHostEventType
    object_id: None | str | Unset = UNSET
    payload: AgentHostEventPayload | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        lease_epoch = self.lease_epoch

        run_id = str(self.run_id)

        sequence = self.sequence

        type_ = self.type_.value

        object_id: None | str | Unset
        if isinstance(self.object_id, Unset):
            object_id = UNSET
        else:
            object_id = self.object_id

        payload: dict[str, Any] | Unset = UNSET
        if not isinstance(self.payload, Unset):
            payload = self.payload.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "lease_epoch": lease_epoch,
                "run_id": run_id,
                "sequence": sequence,
                "type": type_,
            }
        )
        if object_id is not UNSET:
            field_dict["object_id"] = object_id
        if payload is not UNSET:
            field_dict["payload"] = payload

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_event_payload import AgentHostEventPayload

        d = dict(src_dict)
        lease_epoch = d.pop("lease_epoch")

        run_id = UUID(d.pop("run_id"))

        sequence = d.pop("sequence")

        type_ = AgentHostEventType(d.pop("type"))

        def _parse_object_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        object_id = _parse_object_id(d.pop("object_id", UNSET))

        _payload = d.pop("payload", UNSET)
        payload: AgentHostEventPayload | Unset
        if isinstance(_payload, Unset):
            payload = UNSET
        else:
            payload = AgentHostEventPayload.from_dict(_payload)

        agent_host_event = cls(
            lease_epoch=lease_epoch,
            run_id=run_id,
            sequence=sequence,
            type_=type_,
            object_id=object_id,
            payload=payload,
        )

        agent_host_event.additional_properties = d
        return agent_host_event

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
