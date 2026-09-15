from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="WebLoginAuditEntry")


@_attrs_define
class WebLoginAuditEntry:
    """One thing that was done with one saved login.

    `detail` is why, when the outcome was not plain "ok" — "session rejected",
    "nothing for this site was in the browser". It is the platform's own words
    rather than an agent's paraphrase, which is the point of reading it here.

        Attributes:
            action (str):
            conversation_id (None | UUID): The run that did it, or null for something the person did themselves from the
                saved-logins screen.
            created_at (datetime.datetime):
            detail (None | str):
            origin (str):
            outcome (str):
    """

    action: str
    conversation_id: None | UUID
    created_at: datetime.datetime
    detail: None | str
    origin: str
    outcome: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action

        conversation_id: None | str
        if isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        created_at = self.created_at.isoformat()

        detail: None | str
        detail = self.detail

        origin = self.origin

        outcome = self.outcome

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "conversation_id": conversation_id,
                "created_at": created_at,
                "detail": detail,
                "origin": origin,
                "outcome": outcome,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = d.pop("action")

        def _parse_conversation_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id"))

        created_at = isoparse(d.pop("created_at"))

        def _parse_detail(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        detail = _parse_detail(d.pop("detail"))

        origin = d.pop("origin")

        outcome = d.pop("outcome")

        web_login_audit_entry = cls(
            action=action,
            conversation_id=conversation_id,
            created_at=created_at,
            detail=detail,
            origin=origin,
            outcome=outcome,
        )

        web_login_audit_entry.additional_properties = d
        return web_login_audit_entry

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
