from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.listening_response_state import ListeningResponseState
from ..types import UNSET, Unset

T = TypeVar("T", bound="ListeningResponse")


@_attrs_define
class ListeningResponse:
    """How a schedule on a connected MCP server's event is hearing from it.

    Attributes:
        state (ListeningResponseState): listening: the server holds the subscription. retrying: renewing it failed and
            is being retried. lapsed: the server no longer tells us anything. pending: the server has not answered yet.
        last_error (None | str | Unset):
        last_event_at (datetime.datetime | None | Unset):
        refresh_before (datetime.datetime | None | Unset):
    """

    state: ListeningResponseState
    last_error: None | str | Unset = UNSET
    last_event_at: datetime.datetime | None | Unset = UNSET
    refresh_before: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        last_event_at: None | str | Unset
        if isinstance(self.last_event_at, Unset):
            last_event_at = UNSET
        elif isinstance(self.last_event_at, datetime.datetime):
            last_event_at = self.last_event_at.isoformat()
        else:
            last_event_at = self.last_event_at

        refresh_before: None | str | Unset
        if isinstance(self.refresh_before, Unset):
            refresh_before = UNSET
        elif isinstance(self.refresh_before, datetime.datetime):
            refresh_before = self.refresh_before.isoformat()
        else:
            refresh_before = self.refresh_before

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if last_event_at is not UNSET:
            field_dict["last_event_at"] = last_event_at
        if refresh_before is not UNSET:
            field_dict["refresh_before"] = refresh_before

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        state = ListeningResponseState(d.pop("state"))

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_last_event_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_event_at_type_0 = isoparse(data)

                return last_event_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_event_at = _parse_last_event_at(d.pop("last_event_at", UNSET))

        def _parse_refresh_before(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                refresh_before_type_0 = isoparse(data)

                return refresh_before_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        refresh_before = _parse_refresh_before(d.pop("refresh_before", UNSET))

        listening_response = cls(
            state=state,
            last_error=last_error,
            last_event_at=last_event_at,
            refresh_before=refresh_before,
        )

        listening_response.additional_properties = d
        return listening_response

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
