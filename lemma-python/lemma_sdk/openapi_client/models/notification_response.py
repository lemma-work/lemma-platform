from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.notification_origin import NotificationOrigin
from ..types import UNSET, Unset

T = TypeVar("T", bound="NotificationResponse")


@_attrs_define
class NotificationResponse:
    """
    Attributes:
        body (str):
        created_at (datetime.datetime):
        id (UUID):
        pod_id (UUID):
        agent_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        origin_id (None | Unset | UUID):
        origin_type (None | NotificationOrigin | Unset):
        read_at (datetime.datetime | None | Unset):
        title (None | str | Unset):
    """

    body: str
    created_at: datetime.datetime
    id: UUID
    pod_id: UUID
    agent_id: None | Unset | UUID = UNSET
    conversation_id: None | Unset | UUID = UNSET
    origin_id: None | Unset | UUID = UNSET
    origin_type: None | NotificationOrigin | Unset = UNSET
    read_at: datetime.datetime | None | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        created_at = self.created_at.isoformat()

        id = str(self.id)

        pod_id = str(self.pod_id)

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        origin_id: None | str | Unset
        if isinstance(self.origin_id, Unset):
            origin_id = UNSET
        elif isinstance(self.origin_id, UUID):
            origin_id = str(self.origin_id)
        else:
            origin_id = self.origin_id

        origin_type: None | str | Unset
        if isinstance(self.origin_type, Unset):
            origin_type = UNSET
        elif isinstance(self.origin_type, NotificationOrigin):
            origin_type = self.origin_type.value
        else:
            origin_type = self.origin_type

        read_at: None | str | Unset
        if isinstance(self.read_at, Unset):
            read_at = UNSET
        elif isinstance(self.read_at, datetime.datetime):
            read_at = self.read_at.isoformat()
        else:
            read_at = self.read_at

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "body": body,
                "created_at": created_at,
                "id": id,
                "pod_id": pod_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if origin_id is not UNSET:
            field_dict["origin_id"] = origin_id
        if origin_type is not UNSET:
            field_dict["origin_type"] = origin_type
        if read_at is not UNSET:
            field_dict["read_at"] = read_at
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        body = d.pop("body")

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        pod_id = UUID(d.pop("pod_id"))

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        def _parse_origin_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                origin_id_type_0 = UUID(data)

                return origin_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        origin_id = _parse_origin_id(d.pop("origin_id", UNSET))

        def _parse_origin_type(data: object) -> None | NotificationOrigin | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                origin_type_type_0 = NotificationOrigin(data)

                return origin_type_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotificationOrigin | Unset, data)

        origin_type = _parse_origin_type(d.pop("origin_type", UNSET))

        def _parse_read_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                read_at_type_0 = isoparse(data)

                return read_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        read_at = _parse_read_at(d.pop("read_at", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        notification_response = cls(
            body=body,
            created_at=created_at,
            id=id,
            pod_id=pod_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            origin_id=origin_id,
            origin_type=origin_type,
            read_at=read_at,
            title=title,
        )

        notification_response.additional_properties = d
        return notification_response

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
