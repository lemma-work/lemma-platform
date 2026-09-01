from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.message_kind import MessageKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.message_response_metadata_type_0 import MessageResponseMetadataType0


T = TypeVar("T", bound="MessageResponse")


@_attrs_define
class MessageResponse:
    """
    Attributes:
        conversation_id (UUID):
        created_at (datetime.datetime):
        id (UUID):
        kind (MessageKind): Discriminates the flat message body.

            A message carries exactly one kind. Textual kinds use ``text``; tool kinds
            use ``tool_name``/``tool_call_id`` plus ``tool_args`` (call) or
            ``tool_result`` (return). There is no nested ``content`` object.
        role (str):
        sequence (int):
        agent_id (None | Unset | UUID):
        agent_run_id (None | Unset | UUID):
        metadata (MessageResponseMetadataType0 | None | Unset):
        sender_user_id (None | Unset | UUID):
        text (None | str | Unset):
        tool_args (Any | None | Unset):
        tool_call_id (None | str | Unset):
        tool_name (None | str | Unset):
        tool_result (Any | None | Unset):
    """

    conversation_id: UUID
    created_at: datetime.datetime
    id: UUID
    kind: MessageKind
    role: str
    sequence: int
    agent_id: None | Unset | UUID = UNSET
    agent_run_id: None | Unset | UUID = UNSET
    metadata: MessageResponseMetadataType0 | None | Unset = UNSET
    sender_user_id: None | Unset | UUID = UNSET
    text: None | str | Unset = UNSET
    tool_args: Any | None | Unset = UNSET
    tool_call_id: None | str | Unset = UNSET
    tool_name: None | str | Unset = UNSET
    tool_result: Any | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.message_response_metadata_type_0 import (
            MessageResponseMetadataType0,
        )

        conversation_id = str(self.conversation_id)

        created_at = self.created_at.isoformat()

        id = str(self.id)

        kind = self.kind.value

        role = self.role

        sequence = self.sequence

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        agent_run_id: None | str | Unset
        if isinstance(self.agent_run_id, Unset):
            agent_run_id = UNSET
        elif isinstance(self.agent_run_id, UUID):
            agent_run_id = str(self.agent_run_id)
        else:
            agent_run_id = self.agent_run_id

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, MessageResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        sender_user_id: None | str | Unset
        if isinstance(self.sender_user_id, Unset):
            sender_user_id = UNSET
        elif isinstance(self.sender_user_id, UUID):
            sender_user_id = str(self.sender_user_id)
        else:
            sender_user_id = self.sender_user_id

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        tool_args: Any | None | Unset
        if isinstance(self.tool_args, Unset):
            tool_args = UNSET
        else:
            tool_args = self.tool_args

        tool_call_id: None | str | Unset
        if isinstance(self.tool_call_id, Unset):
            tool_call_id = UNSET
        else:
            tool_call_id = self.tool_call_id

        tool_name: None | str | Unset
        if isinstance(self.tool_name, Unset):
            tool_name = UNSET
        else:
            tool_name = self.tool_name

        tool_result: Any | None | Unset
        if isinstance(self.tool_result, Unset):
            tool_result = UNSET
        else:
            tool_result = self.tool_result

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "created_at": created_at,
                "id": id,
                "kind": kind,
                "role": role,
                "sequence": sequence,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_run_id is not UNSET:
            field_dict["agent_run_id"] = agent_run_id
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if sender_user_id is not UNSET:
            field_dict["sender_user_id"] = sender_user_id
        if text is not UNSET:
            field_dict["text"] = text
        if tool_args is not UNSET:
            field_dict["tool_args"] = tool_args
        if tool_call_id is not UNSET:
            field_dict["tool_call_id"] = tool_call_id
        if tool_name is not UNSET:
            field_dict["tool_name"] = tool_name
        if tool_result is not UNSET:
            field_dict["tool_result"] = tool_result

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.message_response_metadata_type_0 import (
            MessageResponseMetadataType0,
        )

        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        kind = MessageKind(d.pop("kind"))

        role = d.pop("role")

        sequence = d.pop("sequence")

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

        def _parse_agent_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_run_id_type_0 = UUID(data)

                return agent_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_run_id = _parse_agent_run_id(d.pop("agent_run_id", UNSET))

        def _parse_metadata(
            data: object,
        ) -> MessageResponseMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = MessageResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(MessageResponseMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

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

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        def _parse_tool_args(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        tool_args = _parse_tool_args(d.pop("tool_args", UNSET))

        def _parse_tool_call_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tool_call_id = _parse_tool_call_id(d.pop("tool_call_id", UNSET))

        def _parse_tool_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tool_name = _parse_tool_name(d.pop("tool_name", UNSET))

        def _parse_tool_result(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        tool_result = _parse_tool_result(d.pop("tool_result", UNSET))

        message_response = cls(
            conversation_id=conversation_id,
            created_at=created_at,
            id=id,
            kind=kind,
            role=role,
            sequence=sequence,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            metadata=metadata,
            sender_user_id=sender_user_id,
            text=text,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )

        message_response.additional_properties = d
        return message_response

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
