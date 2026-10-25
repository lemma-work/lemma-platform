from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.schedule_type import ScheduleType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_descriptor_response_input_schema import (
        EventDescriptorResponseInputSchema,
    )
    from ..models.event_descriptor_response_payload_schema import (
        EventDescriptorResponsePayloadSchema,
    )


T = TypeVar("T", bound="EventDescriptorResponse")


@_attrs_define
class EventDescriptorResponse:
    """An MCP Events descriptor, plus the schedule type that serves it.

    Attributes:
        description (str):
        input_schema (EventDescriptorResponseInputSchema):
        name (str):
        payload_schema (EventDescriptorResponsePayloadSchema):
        schedule_type (ScheduleType): Type of schedule source.
        title (str):
        event (None | str | Unset):
        pod_connector (None | str | Unset):
        server (None | str | Unset):
    """

    description: str
    input_schema: EventDescriptorResponseInputSchema
    name: str
    payload_schema: EventDescriptorResponsePayloadSchema
    schedule_type: ScheduleType
    title: str
    event: None | str | Unset = UNSET
    pod_connector: None | str | Unset = UNSET
    server: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        input_schema = self.input_schema.to_dict()

        name = self.name

        payload_schema = self.payload_schema.to_dict()

        schedule_type = self.schedule_type.value

        title = self.title

        event: None | str | Unset
        if isinstance(self.event, Unset):
            event = UNSET
        else:
            event = self.event

        pod_connector: None | str | Unset
        if isinstance(self.pod_connector, Unset):
            pod_connector = UNSET
        else:
            pod_connector = self.pod_connector

        server: None | str | Unset
        if isinstance(self.server, Unset):
            server = UNSET
        else:
            server = self.server

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "input_schema": input_schema,
                "name": name,
                "payload_schema": payload_schema,
                "schedule_type": schedule_type,
                "title": title,
            }
        )
        if event is not UNSET:
            field_dict["event"] = event
        if pod_connector is not UNSET:
            field_dict["pod_connector"] = pod_connector
        if server is not UNSET:
            field_dict["server"] = server

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_descriptor_response_input_schema import (
            EventDescriptorResponseInputSchema,
        )
        from ..models.event_descriptor_response_payload_schema import (
            EventDescriptorResponsePayloadSchema,
        )

        d = dict(src_dict)
        description = d.pop("description")

        input_schema = EventDescriptorResponseInputSchema.from_dict(
            d.pop("input_schema")
        )

        name = d.pop("name")

        payload_schema = EventDescriptorResponsePayloadSchema.from_dict(
            d.pop("payload_schema")
        )

        schedule_type = ScheduleType(d.pop("schedule_type"))

        title = d.pop("title")

        def _parse_event(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event = _parse_event(d.pop("event", UNSET))

        def _parse_pod_connector(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pod_connector = _parse_pod_connector(d.pop("pod_connector", UNSET))

        def _parse_server(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        server = _parse_server(d.pop("server", UNSET))

        event_descriptor_response = cls(
            description=description,
            input_schema=input_schema,
            name=name,
            payload_schema=payload_schema,
            schedule_type=schedule_type,
            title=title,
            event=event,
            pod_connector=pod_connector,
            server=server,
        )

        event_descriptor_response.additional_properties = d
        return event_descriptor_response

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
