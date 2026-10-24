from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.needs_you_kind import NeedsYouKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="NeedsYouItemResponse")


@_attrs_define
class NeedsYouItemResponse:
    """One thing waiting on the member, and where to answer it.

    Exactly one of ``conversation_id``, ``workflow_run_id`` and ``channel_id``
    is set, by kind: a conversation for QUESTION, APPROVAL and PERMISSION; a
    workflow run for FORM and DECISION; a contact's channel for CONTACT.

        Attributes:
            key (str): Stable across reads: `conversation:<id>`, `wait:<id>` or `contact:<channel id>`.
            kind (NeedsYouKind): What the member is asked for, and so where it is answered.
            pod_id (UUID):
            pod_name (str):
            since (datetime.datetime): When it started waiting.
            channel_id (None | Unset | UUID):
            conversation_id (None | Unset | UUID):
            detail (None | str | Unset): Where it is. The conversation's title when `title` is the question; the workflow's
                name; or why the contact asked for a person.
            title (None | str | Unset): What is asked. QUESTION/APPROVAL/PERMISSION: the question or what the agent wants to
                do, else the conversation's title. FORM/DECISION: the decision's question or the form's title, else the
                workflow's name. CONTACT: the contact's name.
            tool_call_id (None | str | Unset): A conversation's pause, as the approval id its decision endpoint takes.
            workflow_run_id (None | Unset | UUID):
    """

    key: str
    kind: NeedsYouKind
    pod_id: UUID
    pod_name: str
    since: datetime.datetime
    channel_id: None | Unset | UUID = UNSET
    conversation_id: None | Unset | UUID = UNSET
    detail: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    tool_call_id: None | str | Unset = UNSET
    workflow_run_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        kind = self.kind.value

        pod_id = str(self.pod_id)

        pod_name = self.pod_name

        since = self.since.isoformat()

        channel_id: None | str | Unset
        if isinstance(self.channel_id, Unset):
            channel_id = UNSET
        elif isinstance(self.channel_id, UUID):
            channel_id = str(self.channel_id)
        else:
            channel_id = self.channel_id

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        tool_call_id: None | str | Unset
        if isinstance(self.tool_call_id, Unset):
            tool_call_id = UNSET
        else:
            tool_call_id = self.tool_call_id

        workflow_run_id: None | str | Unset
        if isinstance(self.workflow_run_id, Unset):
            workflow_run_id = UNSET
        elif isinstance(self.workflow_run_id, UUID):
            workflow_run_id = str(self.workflow_run_id)
        else:
            workflow_run_id = self.workflow_run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "kind": kind,
                "pod_id": pod_id,
                "pod_name": pod_name,
                "since": since,
            }
        )
        if channel_id is not UNSET:
            field_dict["channel_id"] = channel_id
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if detail is not UNSET:
            field_dict["detail"] = detail
        if title is not UNSET:
            field_dict["title"] = title
        if tool_call_id is not UNSET:
            field_dict["tool_call_id"] = tool_call_id
        if workflow_run_id is not UNSET:
            field_dict["workflow_run_id"] = workflow_run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        key = d.pop("key")

        kind = NeedsYouKind(d.pop("kind"))

        pod_id = UUID(d.pop("pod_id"))

        pod_name = d.pop("pod_name")

        since = isoparse(d.pop("since"))

        def _parse_channel_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                channel_id_type_0 = UUID(data)

                return channel_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        channel_id = _parse_channel_id(d.pop("channel_id", UNSET))

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

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_tool_call_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tool_call_id = _parse_tool_call_id(d.pop("tool_call_id", UNSET))

        def _parse_workflow_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                workflow_run_id_type_0 = UUID(data)

                return workflow_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        workflow_run_id = _parse_workflow_run_id(d.pop("workflow_run_id", UNSET))

        needs_you_item_response = cls(
            key=key,
            kind=kind,
            pod_id=pod_id,
            pod_name=pod_name,
            since=since,
            channel_id=channel_id,
            conversation_id=conversation_id,
            detail=detail,
            title=title,
            tool_call_id=tool_call_id,
            workflow_run_id=workflow_run_id,
        )

        needs_you_item_response.additional_properties = d
        return needs_you_item_response

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
