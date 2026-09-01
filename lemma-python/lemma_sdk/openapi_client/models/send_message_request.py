from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

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
        agent_name (None | str | Unset):
        branch_from_run_id (None | Unset | UUID):
        metadata (None | SendMessageRequestMetadataType0 | Unset):
    """

    content: str
    agent_name: None | str | Unset = UNSET
    branch_from_run_id: None | Unset | UUID = UNSET
    metadata: None | SendMessageRequestMetadataType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.send_message_request_metadata_type_0 import (
            SendMessageRequestMetadataType0,
        )

        content = self.content

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        branch_from_run_id: None | str | Unset
        if isinstance(self.branch_from_run_id, Unset):
            branch_from_run_id = UNSET
        elif isinstance(self.branch_from_run_id, UUID):
            branch_from_run_id = str(self.branch_from_run_id)
        else:
            branch_from_run_id = self.branch_from_run_id

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
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if branch_from_run_id is not UNSET:
            field_dict["branch_from_run_id"] = branch_from_run_id
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

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        def _parse_branch_from_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                branch_from_run_id_type_0 = UUID(data)

                return branch_from_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        branch_from_run_id = _parse_branch_from_run_id(
            d.pop("branch_from_run_id", UNSET)
        )

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
            agent_name=agent_name,
            branch_from_run_id=branch_from_run_id,
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
