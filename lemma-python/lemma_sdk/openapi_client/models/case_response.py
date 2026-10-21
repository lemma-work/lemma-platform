from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.contact_status import ContactStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.attention_response import AttentionResponse
    from ..models.case_event_response import CaseEventResponse
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="CaseResponse")


@_attrs_define
class CaseResponse:
    """
    Attributes:
        id (UUID):
        opened_at (datetime.datetime):
        opened_seq (int): The channel's newest line when it opened: the case is the lines after.
        status (ContactStatus): Where a contact's conversation stands, as the pod's inbox sees it.
        assigned_at (datetime.datetime | None | Unset):
        assignee (None | PersonRef | Unset):
        attention (AttentionResponse | None | Unset):
        closed_at (datetime.datetime | None | Unset):
        closed_by (None | PersonRef | Unset):
        events (list[CaseEventResponse] | Unset): Oldest first; the newest 200 of a long case.
        first_response_at (datetime.datetime | None | Unset): The first member line to the contact in it.
        first_response_by (None | PersonRef | Unset):
    """

    id: UUID
    opened_at: datetime.datetime
    opened_seq: int
    status: ContactStatus
    assigned_at: datetime.datetime | None | Unset = UNSET
    assignee: None | PersonRef | Unset = UNSET
    attention: AttentionResponse | None | Unset = UNSET
    closed_at: datetime.datetime | None | Unset = UNSET
    closed_by: None | PersonRef | Unset = UNSET
    events: list[CaseEventResponse] | Unset = UNSET
    first_response_at: datetime.datetime | None | Unset = UNSET
    first_response_by: None | PersonRef | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.attention_response import AttentionResponse
        from ..models.person_ref import PersonRef

        id = str(self.id)

        opened_at = self.opened_at.isoformat()

        opened_seq = self.opened_seq

        status = self.status.value

        assigned_at: None | str | Unset
        if isinstance(self.assigned_at, Unset):
            assigned_at = UNSET
        elif isinstance(self.assigned_at, datetime.datetime):
            assigned_at = self.assigned_at.isoformat()
        else:
            assigned_at = self.assigned_at

        assignee: dict[str, Any] | None | Unset
        if isinstance(self.assignee, Unset):
            assignee = UNSET
        elif isinstance(self.assignee, PersonRef):
            assignee = self.assignee.to_dict()
        else:
            assignee = self.assignee

        attention: dict[str, Any] | None | Unset
        if isinstance(self.attention, Unset):
            attention = UNSET
        elif isinstance(self.attention, AttentionResponse):
            attention = self.attention.to_dict()
        else:
            attention = self.attention

        closed_at: None | str | Unset
        if isinstance(self.closed_at, Unset):
            closed_at = UNSET
        elif isinstance(self.closed_at, datetime.datetime):
            closed_at = self.closed_at.isoformat()
        else:
            closed_at = self.closed_at

        closed_by: dict[str, Any] | None | Unset
        if isinstance(self.closed_by, Unset):
            closed_by = UNSET
        elif isinstance(self.closed_by, PersonRef):
            closed_by = self.closed_by.to_dict()
        else:
            closed_by = self.closed_by

        events: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.events, Unset):
            events = []
            for events_item_data in self.events:
                events_item = events_item_data.to_dict()
                events.append(events_item)

        first_response_at: None | str | Unset
        if isinstance(self.first_response_at, Unset):
            first_response_at = UNSET
        elif isinstance(self.first_response_at, datetime.datetime):
            first_response_at = self.first_response_at.isoformat()
        else:
            first_response_at = self.first_response_at

        first_response_by: dict[str, Any] | None | Unset
        if isinstance(self.first_response_by, Unset):
            first_response_by = UNSET
        elif isinstance(self.first_response_by, PersonRef):
            first_response_by = self.first_response_by.to_dict()
        else:
            first_response_by = self.first_response_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "opened_at": opened_at,
                "opened_seq": opened_seq,
                "status": status,
            }
        )
        if assigned_at is not UNSET:
            field_dict["assigned_at"] = assigned_at
        if assignee is not UNSET:
            field_dict["assignee"] = assignee
        if attention is not UNSET:
            field_dict["attention"] = attention
        if closed_at is not UNSET:
            field_dict["closed_at"] = closed_at
        if closed_by is not UNSET:
            field_dict["closed_by"] = closed_by
        if events is not UNSET:
            field_dict["events"] = events
        if first_response_at is not UNSET:
            field_dict["first_response_at"] = first_response_at
        if first_response_by is not UNSET:
            field_dict["first_response_by"] = first_response_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.attention_response import AttentionResponse
        from ..models.case_event_response import CaseEventResponse
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        opened_at = isoparse(d.pop("opened_at"))

        opened_seq = d.pop("opened_seq")

        status = ContactStatus(d.pop("status"))

        def _parse_assigned_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assigned_at_type_0 = isoparse(data)

                return assigned_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        assigned_at = _parse_assigned_at(d.pop("assigned_at", UNSET))

        def _parse_assignee(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                assignee_type_0 = PersonRef.from_dict(data)

                return assignee_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        assignee = _parse_assignee(d.pop("assignee", UNSET))

        def _parse_attention(data: object) -> AttentionResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attention_type_0 = AttentionResponse.from_dict(data)

                return attention_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AttentionResponse | None | Unset, data)

        attention = _parse_attention(d.pop("attention", UNSET))

        def _parse_closed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                closed_at_type_0 = isoparse(data)

                return closed_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        closed_at = _parse_closed_at(d.pop("closed_at", UNSET))

        def _parse_closed_by(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                closed_by_type_0 = PersonRef.from_dict(data)

                return closed_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        closed_by = _parse_closed_by(d.pop("closed_by", UNSET))

        _events = d.pop("events", UNSET)
        events: list[CaseEventResponse] | Unset = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = CaseEventResponse.from_dict(events_item_data)

                events.append(events_item)

        def _parse_first_response_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                first_response_at_type_0 = isoparse(data)

                return first_response_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        first_response_at = _parse_first_response_at(d.pop("first_response_at", UNSET))

        def _parse_first_response_by(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                first_response_by_type_0 = PersonRef.from_dict(data)

                return first_response_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        first_response_by = _parse_first_response_by(d.pop("first_response_by", UNSET))

        case_response = cls(
            id=id,
            opened_at=opened_at,
            opened_seq=opened_seq,
            status=status,
            assigned_at=assigned_at,
            assignee=assignee,
            attention=attention,
            closed_at=closed_at,
            closed_by=closed_by,
            events=events,
            first_response_at=first_response_at,
            first_response_by=first_response_by,
        )

        case_response.additional_properties = d
        return case_response

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
