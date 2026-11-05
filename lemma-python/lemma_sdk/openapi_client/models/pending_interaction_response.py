from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.conversation_status import ConversationStatus
from ..models.pending_interaction_kind import PendingInteractionKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="PendingInteractionResponse")


@_attrs_define
class PendingInteractionResponse:
    """One pause waiting on the caller, and the conversation it is in.

    Attributes:
        conversation_id (UUID):
        created_at (datetime.datetime):
        kind (PendingInteractionKind): What the person is being asked for.
        tool_call_id (str): The paused call, which is also the approval id the decision endpoint takes.
        agent_id (None | Unset | UUID): Absent for the pod's own assistant on older conversations.
        agent_name (None | str | Unset):
        conversation_status (ConversationStatus | None | Unset):
        conversation_title (None | str | Unset):
        summary (None | str | Unset): The question asked, or the approval's title, on one line.
    """

    conversation_id: UUID
    created_at: datetime.datetime
    kind: PendingInteractionKind
    tool_call_id: str
    agent_id: None | Unset | UUID = UNSET
    agent_name: None | str | Unset = UNSET
    conversation_status: ConversationStatus | None | Unset = UNSET
    conversation_title: None | str | Unset = UNSET
    summary: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        created_at = self.created_at.isoformat()

        kind = self.kind.value

        tool_call_id = self.tool_call_id

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        conversation_status: None | str | Unset
        if isinstance(self.conversation_status, Unset):
            conversation_status = UNSET
        elif isinstance(self.conversation_status, ConversationStatus):
            conversation_status = self.conversation_status.value
        else:
            conversation_status = self.conversation_status

        conversation_title: None | str | Unset
        if isinstance(self.conversation_title, Unset):
            conversation_title = UNSET
        else:
            conversation_title = self.conversation_title

        summary: None | str | Unset
        if isinstance(self.summary, Unset):
            summary = UNSET
        else:
            summary = self.summary

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "created_at": created_at,
                "kind": kind,
                "tool_call_id": tool_call_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if conversation_status is not UNSET:
            field_dict["conversation_status"] = conversation_status
        if conversation_title is not UNSET:
            field_dict["conversation_title"] = conversation_title
        if summary is not UNSET:
            field_dict["summary"] = summary

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        created_at = isoparse(d.pop("created_at"))

        kind = PendingInteractionKind(d.pop("kind"))

        tool_call_id = d.pop("tool_call_id")

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

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        def _parse_conversation_status(
            data: object,
        ) -> ConversationStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_status_type_0 = ConversationStatus(data)

                return conversation_status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ConversationStatus | None | Unset, data)

        conversation_status = _parse_conversation_status(
            d.pop("conversation_status", UNSET)
        )

        def _parse_conversation_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        conversation_title = _parse_conversation_title(
            d.pop("conversation_title", UNSET)
        )

        def _parse_summary(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        summary = _parse_summary(d.pop("summary", UNSET))

        pending_interaction_response = cls(
            conversation_id=conversation_id,
            created_at=created_at,
            kind=kind,
            tool_call_id=tool_call_id,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_status=conversation_status,
            conversation_title=conversation_title,
            summary=summary,
        )

        pending_interaction_response.additional_properties = d
        return pending_interaction_response

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
