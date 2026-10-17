from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.sender_kind import SenderKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChannelMessageResponse")


@_attrs_define
class ChannelMessageResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        sender_kind (SenderKind):
        text (str):
        members_only (bool | Unset):  Default: False.
        on_behalf_contact_id (None | Unset | UUID): For an agent's line: the contact it answered.
        on_behalf_user_id (None | Unset | UUID): For an agent's line: the member it acted for.
        sender_agent_id (None | Unset | UUID):
        sender_contact_id (None | Unset | UUID):
        sender_name (None | str | Unset):
        sender_user_id (None | Unset | UUID):
        source_id (None | Unset | UUID): Where it was said; absent for a line written in Lemma.
    """

    created_at: datetime.datetime
    id: UUID
    sender_kind: SenderKind
    text: str
    members_only: bool | Unset = False
    on_behalf_contact_id: None | Unset | UUID = UNSET
    on_behalf_user_id: None | Unset | UUID = UNSET
    sender_agent_id: None | Unset | UUID = UNSET
    sender_contact_id: None | Unset | UUID = UNSET
    sender_name: None | str | Unset = UNSET
    sender_user_id: None | Unset | UUID = UNSET
    source_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        sender_kind = self.sender_kind.value

        text = self.text

        members_only = self.members_only

        on_behalf_contact_id: None | str | Unset
        if isinstance(self.on_behalf_contact_id, Unset):
            on_behalf_contact_id = UNSET
        elif isinstance(self.on_behalf_contact_id, UUID):
            on_behalf_contact_id = str(self.on_behalf_contact_id)
        else:
            on_behalf_contact_id = self.on_behalf_contact_id

        on_behalf_user_id: None | str | Unset
        if isinstance(self.on_behalf_user_id, Unset):
            on_behalf_user_id = UNSET
        elif isinstance(self.on_behalf_user_id, UUID):
            on_behalf_user_id = str(self.on_behalf_user_id)
        else:
            on_behalf_user_id = self.on_behalf_user_id

        sender_agent_id: None | str | Unset
        if isinstance(self.sender_agent_id, Unset):
            sender_agent_id = UNSET
        elif isinstance(self.sender_agent_id, UUID):
            sender_agent_id = str(self.sender_agent_id)
        else:
            sender_agent_id = self.sender_agent_id

        sender_contact_id: None | str | Unset
        if isinstance(self.sender_contact_id, Unset):
            sender_contact_id = UNSET
        elif isinstance(self.sender_contact_id, UUID):
            sender_contact_id = str(self.sender_contact_id)
        else:
            sender_contact_id = self.sender_contact_id

        sender_name: None | str | Unset
        if isinstance(self.sender_name, Unset):
            sender_name = UNSET
        else:
            sender_name = self.sender_name

        sender_user_id: None | str | Unset
        if isinstance(self.sender_user_id, Unset):
            sender_user_id = UNSET
        elif isinstance(self.sender_user_id, UUID):
            sender_user_id = str(self.sender_user_id)
        else:
            sender_user_id = self.sender_user_id

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "sender_kind": sender_kind,
                "text": text,
            }
        )
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if on_behalf_contact_id is not UNSET:
            field_dict["on_behalf_contact_id"] = on_behalf_contact_id
        if on_behalf_user_id is not UNSET:
            field_dict["on_behalf_user_id"] = on_behalf_user_id
        if sender_agent_id is not UNSET:
            field_dict["sender_agent_id"] = sender_agent_id
        if sender_contact_id is not UNSET:
            field_dict["sender_contact_id"] = sender_contact_id
        if sender_name is not UNSET:
            field_dict["sender_name"] = sender_name
        if sender_user_id is not UNSET:
            field_dict["sender_user_id"] = sender_user_id
        if source_id is not UNSET:
            field_dict["source_id"] = source_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        sender_kind = SenderKind(d.pop("sender_kind"))

        text = d.pop("text")

        members_only = d.pop("members_only", UNSET)

        def _parse_on_behalf_contact_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                on_behalf_contact_id_type_0 = UUID(data)

                return on_behalf_contact_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        on_behalf_contact_id = _parse_on_behalf_contact_id(
            d.pop("on_behalf_contact_id", UNSET)
        )

        def _parse_on_behalf_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                on_behalf_user_id_type_0 = UUID(data)

                return on_behalf_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        on_behalf_user_id = _parse_on_behalf_user_id(d.pop("on_behalf_user_id", UNSET))

        def _parse_sender_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                sender_agent_id_type_0 = UUID(data)

                return sender_agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        sender_agent_id = _parse_sender_agent_id(d.pop("sender_agent_id", UNSET))

        def _parse_sender_contact_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                sender_contact_id_type_0 = UUID(data)

                return sender_contact_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        sender_contact_id = _parse_sender_contact_id(d.pop("sender_contact_id", UNSET))

        def _parse_sender_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sender_name = _parse_sender_name(d.pop("sender_name", UNSET))

        def _parse_sender_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                sender_user_id_type_0 = UUID(data)

                return sender_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        sender_user_id = _parse_sender_user_id(d.pop("sender_user_id", UNSET))

        def _parse_source_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_id_type_0 = UUID(data)

                return source_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        channel_message_response = cls(
            created_at=created_at,
            id=id,
            sender_kind=sender_kind,
            text=text,
            members_only=members_only,
            on_behalf_contact_id=on_behalf_contact_id,
            on_behalf_user_id=on_behalf_user_id,
            sender_agent_id=sender_agent_id,
            sender_contact_id=sender_contact_id,
            sender_name=sender_name,
            sender_user_id=sender_user_id,
            source_id=source_id,
        )

        channel_message_response.additional_properties = d
        return channel_message_response

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
