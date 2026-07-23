from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.app_status import AppStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="AppDetailResponse")


@_attrs_define
class AppDetailResponse:
    """
    Attributes:
        created_at (Any):
        id (UUID):
        name (str):
        pod_id (UUID):
        public_slug (str):
        status (AppStatus):
        updated_at (Any):
        url (str):
        user_id (UUID):
        allowed_actions (list[str] | Unset):
        current_release_id (None | Unset | UUID):
        description (None | str | Unset):
        source_archive_path (None | str | Unset):
        visibility (str | Unset):  Default: 'POD'.
    """

    created_at: Any
    id: UUID
    name: str
    pod_id: UUID
    public_slug: str
    status: AppStatus
    updated_at: Any
    url: str
    user_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    current_release_id: None | Unset | UUID = UNSET
    description: None | str | Unset = UNSET
    source_archive_path: None | str | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at

        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        public_slug = self.public_slug

        status = self.status.value

        updated_at = self.updated_at

        url = self.url

        user_id = str(self.user_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        current_release_id: None | str | Unset
        if isinstance(self.current_release_id, Unset):
            current_release_id = UNSET
        elif isinstance(self.current_release_id, UUID):
            current_release_id = str(self.current_release_id)
        else:
            current_release_id = self.current_release_id

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        source_archive_path: None | str | Unset
        if isinstance(self.source_archive_path, Unset):
            source_archive_path = UNSET
        else:
            source_archive_path = self.source_archive_path

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "pod_id": pod_id,
                "public_slug": public_slug,
                "status": status,
                "updated_at": updated_at,
                "url": url,
                "user_id": user_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if current_release_id is not UNSET:
            field_dict["current_release_id"] = current_release_id
        if description is not UNSET:
            field_dict["description"] = description
        if source_archive_path is not UNSET:
            field_dict["source_archive_path"] = source_archive_path
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = d.pop("created_at")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        public_slug = d.pop("public_slug")

        status = AppStatus(d.pop("status"))

        updated_at = d.pop("updated_at")

        url = d.pop("url")

        user_id = UUID(d.pop("user_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_current_release_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                current_release_id_type_0 = UUID(data)

                return current_release_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        current_release_id = _parse_current_release_id(
            d.pop("current_release_id", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_source_archive_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_archive_path = _parse_source_archive_path(
            d.pop("source_archive_path", UNSET)
        )

        visibility = d.pop("visibility", UNSET)

        app_detail_response = cls(
            created_at=created_at,
            id=id,
            name=name,
            pod_id=pod_id,
            public_slug=public_slug,
            status=status,
            updated_at=updated_at,
            url=url,
            user_id=user_id,
            allowed_actions=allowed_actions,
            current_release_id=current_release_id,
            description=description,
            source_archive_path=source_archive_path,
            visibility=visibility,
        )

        app_detail_response.additional_properties = d
        return app_detail_response

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
