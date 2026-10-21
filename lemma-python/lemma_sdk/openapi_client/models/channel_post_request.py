from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_part import FilePart
    from ..models.link_part import LinkPart
    from ..models.mention_part import MentionPart


T = TypeVar("T", bound="ChannelPostRequest")


@_attrs_define
class ChannelPostRequest:
    """
    Attributes:
        client_message_id (None | str | Unset): The client's name for this line: sending it again is the same line.
            Shown back on the line to its writer, as sent.
        members_only (bool | Unset): A note to the pod's members: never sent outside. Default: False.
        parts (list[FilePart | LinkPart | MentionPart] | Unset): Mentions, files and links. Who a line names is read
            from here.
        reply_to_id (None | Unset | UUID):
        source_id (None | Unset | UUID): Which outside place it goes to; by default the thread's, else the one the
            channel last heard from.
        text (str | Unset):  Default: ''.
        thread_root_id (None | Unset | UUID): Reply in the thread of this line.
    """

    client_message_id: None | str | Unset = UNSET
    members_only: bool | Unset = False
    parts: list[FilePart | LinkPart | MentionPart] | Unset = UNSET
    reply_to_id: None | Unset | UUID = UNSET
    source_id: None | Unset | UUID = UNSET
    text: str | Unset = ""
    thread_root_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.file_part import FilePart
        from ..models.mention_part import MentionPart

        client_message_id: None | str | Unset
        if isinstance(self.client_message_id, Unset):
            client_message_id = UNSET
        else:
            client_message_id = self.client_message_id

        members_only = self.members_only

        parts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.parts, Unset):
            parts = []
            for parts_item_data in self.parts:
                parts_item: dict[str, Any]
                if isinstance(parts_item_data, MentionPart):
                    parts_item = parts_item_data.to_dict()
                elif isinstance(parts_item_data, FilePart):
                    parts_item = parts_item_data.to_dict()
                else:
                    parts_item = parts_item_data.to_dict()

                parts.append(parts_item)

        reply_to_id: None | str | Unset
        if isinstance(self.reply_to_id, Unset):
            reply_to_id = UNSET
        elif isinstance(self.reply_to_id, UUID):
            reply_to_id = str(self.reply_to_id)
        else:
            reply_to_id = self.reply_to_id

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        text = self.text

        thread_root_id: None | str | Unset
        if isinstance(self.thread_root_id, Unset):
            thread_root_id = UNSET
        elif isinstance(self.thread_root_id, UUID):
            thread_root_id = str(self.thread_root_id)
        else:
            thread_root_id = self.thread_root_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if client_message_id is not UNSET:
            field_dict["client_message_id"] = client_message_id
        if members_only is not UNSET:
            field_dict["members_only"] = members_only
        if parts is not UNSET:
            field_dict["parts"] = parts
        if reply_to_id is not UNSET:
            field_dict["reply_to_id"] = reply_to_id
        if source_id is not UNSET:
            field_dict["source_id"] = source_id
        if text is not UNSET:
            field_dict["text"] = text
        if thread_root_id is not UNSET:
            field_dict["thread_root_id"] = thread_root_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_part import FilePart
        from ..models.link_part import LinkPart
        from ..models.mention_part import MentionPart

        d = dict(src_dict)

        def _parse_client_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_message_id = _parse_client_message_id(d.pop("client_message_id", UNSET))

        members_only = d.pop("members_only", UNSET)

        _parts = d.pop("parts", UNSET)
        parts: list[FilePart | LinkPart | MentionPart] | Unset = UNSET
        if _parts is not UNSET:
            parts = []
            for parts_item_data in _parts:

                def _parse_parts_item(
                    data: object,
                ) -> FilePart | LinkPart | MentionPart:
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        parts_item_type_0 = MentionPart.from_dict(data)

                        return parts_item_type_0
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        parts_item_type_1 = FilePart.from_dict(data)

                        return parts_item_type_1
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    parts_item_type_2 = LinkPart.from_dict(data)

                    return parts_item_type_2

                parts_item = _parse_parts_item(parts_item_data)

                parts.append(parts_item)

        def _parse_reply_to_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                reply_to_id_type_0 = UUID(data)

                return reply_to_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        reply_to_id = _parse_reply_to_id(d.pop("reply_to_id", UNSET))

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

        text = d.pop("text", UNSET)

        def _parse_thread_root_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                thread_root_id_type_0 = UUID(data)

                return thread_root_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        thread_root_id = _parse_thread_root_id(d.pop("thread_root_id", UNSET))

        channel_post_request = cls(
            client_message_id=client_message_id,
            members_only=members_only,
            parts=parts,
            reply_to_id=reply_to_id,
            source_id=source_id,
            text=text,
            thread_root_id=thread_root_id,
        )

        channel_post_request.additional_properties = d
        return channel_post_request

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
