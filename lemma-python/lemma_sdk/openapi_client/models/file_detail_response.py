from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_detail_response_metadata_type_0 import (
        FileDetailResponseMetadataType0,
    )


T = TypeVar("T", bound="FileDetailResponse")


@_attrs_define
class FileDetailResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        description (None | str):
        id (UUID):
        kind (str):
        name (str):
        path (str):
        pod_id (UUID):
        status (str):
        updated_at (datetime.datetime):
        allowed_actions (list[str] | Unset):
        content_sha256 (None | str | Unset):
        indexed_at (datetime.datetime | None | Unset):
        last_processing_error (None | str | Unset):
        metadata (FileDetailResponseMetadataType0 | None | Unset):
        mime_type (None | str | Unset):
        owner_user_id (None | Unset | UUID):
        processing_attempts (int | Unset):  Default: 0.
        search_enabled (bool | Unset):  Default: True.
        size_bytes (int | Unset):  Default: 0.
        visibility (str | Unset):  Default: 'PERSONAL'.
    """

    created_at: datetime.datetime
    description: None | str
    id: UUID
    kind: str
    name: str
    path: str
    pod_id: UUID
    status: str
    updated_at: datetime.datetime
    allowed_actions: list[str] | Unset = UNSET
    content_sha256: None | str | Unset = UNSET
    indexed_at: datetime.datetime | None | Unset = UNSET
    last_processing_error: None | str | Unset = UNSET
    metadata: FileDetailResponseMetadataType0 | None | Unset = UNSET
    mime_type: None | str | Unset = UNSET
    owner_user_id: None | Unset | UUID = UNSET
    processing_attempts: int | Unset = 0
    search_enabled: bool | Unset = True
    size_bytes: int | Unset = 0
    visibility: str | Unset = "PERSONAL"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.file_detail_response_metadata_type_0 import (
            FileDetailResponseMetadataType0,
        )

        created_at = self.created_at.isoformat()

        description: None | str
        description = self.description

        id = str(self.id)

        kind = self.kind

        name = self.name

        path = self.path

        pod_id = str(self.pod_id)

        status = self.status

        updated_at = self.updated_at.isoformat()

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        content_sha256: None | str | Unset
        if isinstance(self.content_sha256, Unset):
            content_sha256 = UNSET
        else:
            content_sha256 = self.content_sha256

        indexed_at: None | str | Unset
        if isinstance(self.indexed_at, Unset):
            indexed_at = UNSET
        elif isinstance(self.indexed_at, datetime.datetime):
            indexed_at = self.indexed_at.isoformat()
        else:
            indexed_at = self.indexed_at

        last_processing_error: None | str | Unset
        if isinstance(self.last_processing_error, Unset):
            last_processing_error = UNSET
        else:
            last_processing_error = self.last_processing_error

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, FileDetailResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        mime_type: None | str | Unset
        if isinstance(self.mime_type, Unset):
            mime_type = UNSET
        else:
            mime_type = self.mime_type

        owner_user_id: None | str | Unset
        if isinstance(self.owner_user_id, Unset):
            owner_user_id = UNSET
        elif isinstance(self.owner_user_id, UUID):
            owner_user_id = str(self.owner_user_id)
        else:
            owner_user_id = self.owner_user_id

        processing_attempts = self.processing_attempts

        search_enabled = self.search_enabled

        size_bytes = self.size_bytes

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "description": description,
                "id": id,
                "kind": kind,
                "name": name,
                "path": path,
                "pod_id": pod_id,
                "status": status,
                "updated_at": updated_at,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if content_sha256 is not UNSET:
            field_dict["content_sha256"] = content_sha256
        if indexed_at is not UNSET:
            field_dict["indexed_at"] = indexed_at
        if last_processing_error is not UNSET:
            field_dict["last_processing_error"] = last_processing_error
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if mime_type is not UNSET:
            field_dict["mime_type"] = mime_type
        if owner_user_id is not UNSET:
            field_dict["owner_user_id"] = owner_user_id
        if processing_attempts is not UNSET:
            field_dict["processing_attempts"] = processing_attempts
        if search_enabled is not UNSET:
            field_dict["search_enabled"] = search_enabled
        if size_bytes is not UNSET:
            field_dict["size_bytes"] = size_bytes
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_detail_response_metadata_type_0 import (
            FileDetailResponseMetadataType0,
        )

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        id = UUID(d.pop("id"))

        kind = d.pop("kind")

        name = d.pop("name")

        path = d.pop("path")

        pod_id = UUID(d.pop("pod_id"))

        status = d.pop("status")

        updated_at = isoparse(d.pop("updated_at"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_content_sha256(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        content_sha256 = _parse_content_sha256(d.pop("content_sha256", UNSET))

        def _parse_indexed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                indexed_at_type_0 = isoparse(data)

                return indexed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        indexed_at = _parse_indexed_at(d.pop("indexed_at", UNSET))

        def _parse_last_processing_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_processing_error = _parse_last_processing_error(
            d.pop("last_processing_error", UNSET)
        )

        def _parse_metadata(
            data: object,
        ) -> FileDetailResponseMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = FileDetailResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(FileDetailResponseMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_mime_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mime_type = _parse_mime_type(d.pop("mime_type", UNSET))

        def _parse_owner_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_user_id_type_0 = UUID(data)

                return owner_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        owner_user_id = _parse_owner_user_id(d.pop("owner_user_id", UNSET))

        processing_attempts = d.pop("processing_attempts", UNSET)

        search_enabled = d.pop("search_enabled", UNSET)

        size_bytes = d.pop("size_bytes", UNSET)

        visibility = d.pop("visibility", UNSET)

        file_detail_response = cls(
            created_at=created_at,
            description=description,
            id=id,
            kind=kind,
            name=name,
            path=path,
            pod_id=pod_id,
            status=status,
            updated_at=updated_at,
            allowed_actions=allowed_actions,
            content_sha256=content_sha256,
            indexed_at=indexed_at,
            last_processing_error=last_processing_error,
            metadata=metadata,
            mime_type=mime_type,
            owner_user_id=owner_user_id,
            processing_attempts=processing_attempts,
            search_enabled=search_enabled,
            size_bytes=size_bytes,
            visibility=visibility,
        )

        file_detail_response.additional_properties = d
        return file_detail_response

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
