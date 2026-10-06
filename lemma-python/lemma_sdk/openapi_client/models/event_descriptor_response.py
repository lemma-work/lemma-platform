from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

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
        account_id (None | Unset | UUID):
        event (None | str | Unset):
        server (None | str | Unset):
    """

    description: str
    input_schema: EventDescriptorResponseInputSchema
    name: str
    payload_schema: EventDescriptorResponsePayloadSchema
    schedule_type: ScheduleType
    title: str
    account_id: None | Unset | UUID = UNSET
    event: None | str | Unset = UNSET
    server: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        input_schema = self.input_schema.to_dict()

        name = self.name

        payload_schema = self.payload_schema.to_dict()

        schedule_type = self.schedule_type.value

        title = self.title

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        event: None | str | Unset
        if isinstance(self.event, Unset):
            event = UNSET
        else:
            event = self.event

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
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if event is not UNSET:
            field_dict["event"] = event
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

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_event(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event = _parse_event(d.pop("event", UNSET))

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
            account_id=account_id,
            event=event,
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
