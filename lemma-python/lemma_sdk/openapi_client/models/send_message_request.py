from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.send_message_request_metadata_type_0 import (
        SendMessageRequestMetadataType0,
    )


T = TypeVar("T", bound="SendMessageRequest")


@_attrs_define
class SendMessageRequest:
    """
    Attributes:
        content (str):
        client_message_id (None | str | Unset): The sender's own id for this send, generated once per message the person
            sends and reused on every retry of it. A second send with an id already used in this conversation stores nothing
            and returns the first send's run, with deduplicated=true.
        metadata (None | SendMessageRequestMetadataType0 | Unset):
    """

    content: str
    client_message_id: None | str | Unset = UNSET
    metadata: None | SendMessageRequestMetadataType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.send_message_request_metadata_type_0 import (
            SendMessageRequestMetadataType0,
        )

        content = self.content

        client_message_id: None | str | Unset
        if isinstance(self.client_message_id, Unset):
            client_message_id = UNSET
        else:
            client_message_id = self.client_message_id

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, SendMessageRequestMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "content": content,
            }
        )
        if client_message_id is not UNSET:
            field_dict["client_message_id"] = client_message_id
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.send_message_request_metadata_type_0 import (
            SendMessageRequestMetadataType0,
        )

        d = dict(src_dict)
        content = d.pop("content")

        def _parse_client_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_message_id = _parse_client_message_id(d.pop("client_message_id", UNSET))

        def _parse_metadata(
            data: object,
        ) -> None | SendMessageRequestMetadataType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = SendMessageRequestMetadataType0.from_dict(data)

                return metadata_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SendMessageRequestMetadataType0 | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        send_message_request = cls(
            content=content,
            client_message_id=client_message_id,
            metadata=metadata,
        )

        send_message_request.additional_properties = d
        return send_message_request

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
