from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.case_event_response_value import CaseEventResponseValue
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="CaseEventResponse")


@_attrs_define
class CaseEventResponse:
    """
    Attributes:
        at (datetime.datetime):
        kind (str): `opened`, `assigned`, `handed_back`, `attention`, `acknowledged`, `first_response`, `status`,
            `closed`, `reopened` or `merged`.
        actor (None | PersonRef | Unset): The member who made it, when one did.
        value (CaseEventResponseValue | Unset): What it changed to: `status`, `user_id`, `reason`, `seq`, ...
    """

    at: datetime.datetime
    kind: str
    actor: None | PersonRef | Unset = UNSET
    value: CaseEventResponseValue | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef

        at = self.at.isoformat()

        kind = self.kind

        actor: dict[str, Any] | None | Unset
        if isinstance(self.actor, Unset):
            actor = UNSET
        elif isinstance(self.actor, PersonRef):
            actor = self.actor.to_dict()
        else:
            actor = self.actor

        value: dict[str, Any] | Unset = UNSET
        if not isinstance(self.value, Unset):
            value = self.value.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "at": at,
                "kind": kind,
            }
        )
        if actor is not UNSET:
            field_dict["actor"] = actor
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.case_event_response_value import CaseEventResponseValue
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        at = isoparse(d.pop("at"))

        kind = d.pop("kind")

        def _parse_actor(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                actor_type_0 = PersonRef.from_dict(data)

                return actor_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        actor = _parse_actor(d.pop("actor", UNSET))

        _value = d.pop("value", UNSET)
        value: CaseEventResponseValue | Unset
        if isinstance(_value, Unset):
            value = UNSET
        else:
            value = CaseEventResponseValue.from_dict(_value)

        case_event_response = cls(
            at=at,
            kind=kind,
            actor=actor,
            value=value,
        )

        case_event_response.additional_properties = d
        return case_event_response

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
