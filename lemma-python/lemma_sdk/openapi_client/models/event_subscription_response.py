from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.event_subscription_response_arguments import (
        EventSubscriptionResponseArguments,
    )


T = TypeVar("T", bound="EventSubscriptionResponse")


@_attrs_define
class EventSubscriptionResponse:
    """Something a connected app asked to be told about.

    Attributes:
        arguments (EventSubscriptionResponseArguments):
        id (str):
        last_delivery_at (datetime.datetime | None):
        last_error (None | str):
        name (str):
        refresh_before (datetime.datetime):
    """

    arguments: EventSubscriptionResponseArguments
    id: str
    last_delivery_at: datetime.datetime | None
    last_error: None | str
    name: str
    refresh_before: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        arguments = self.arguments.to_dict()

        id = self.id

        last_delivery_at: None | str
        if isinstance(self.last_delivery_at, datetime.datetime):
            last_delivery_at = self.last_delivery_at.isoformat()
        else:
            last_delivery_at = self.last_delivery_at

        last_error: None | str
        last_error = self.last_error

        name = self.name

        refresh_before = self.refresh_before.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "arguments": arguments,
                "id": id,
                "last_delivery_at": last_delivery_at,
                "last_error": last_error,
                "name": name,
                "refresh_before": refresh_before,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_subscription_response_arguments import (
            EventSubscriptionResponseArguments,
        )

        d = dict(src_dict)
        arguments = EventSubscriptionResponseArguments.from_dict(d.pop("arguments"))

        id = d.pop("id")

        def _parse_last_delivery_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_delivery_at_type_0 = isoparse(data)

                return last_delivery_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_delivery_at = _parse_last_delivery_at(d.pop("last_delivery_at"))

        def _parse_last_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_error = _parse_last_error(d.pop("last_error"))

        name = d.pop("name")

        refresh_before = isoparse(d.pop("refresh_before"))

        event_subscription_response = cls(
            arguments=arguments,
            id=id,
            last_delivery_at=last_delivery_at,
            last_error=last_error,
            name=name,
            refresh_before=refresh_before,
        )

        event_subscription_response.additional_properties = d
        return event_subscription_response

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
