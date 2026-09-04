from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="AppReleaseResponse")


@_attrs_define
class AppReleaseResponse:
    """One entry in an app's release history.

    Attributes:
        app_id (UUID):
        created_at (datetime.datetime | None):
        has_source (bool): Whether this release's own source archive is still stored.
        id (UUID):
        is_live (bool): True for the release this app currently serves.
        preview_url (str):
        release_number (int):
        version (str): sha256 digest of the release's dist archive.
        created_by (None | Unset | UUID):
        label (None | str | Unset):
        pruned_at (datetime.datetime | None | Unset): Set when retention removed this release's build. The entry stays
            in the history, but it can no longer be previewed or promoted.
    """

    app_id: UUID
    created_at: datetime.datetime | None
    has_source: bool
    id: UUID
    is_live: bool
    preview_url: str
    release_number: int
    version: str
    created_by: None | Unset | UUID = UNSET
    label: None | str | Unset = UNSET
    pruned_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_id = str(self.app_id)

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        has_source = self.has_source

        id = str(self.id)

        is_live = self.is_live

        preview_url = self.preview_url

        release_number = self.release_number

        version = self.version

        created_by: None | str | Unset
        if isinstance(self.created_by, Unset):
            created_by = UNSET
        elif isinstance(self.created_by, UUID):
            created_by = str(self.created_by)
        else:
            created_by = self.created_by

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        pruned_at: None | str | Unset
        if isinstance(self.pruned_at, Unset):
            pruned_at = UNSET
        elif isinstance(self.pruned_at, datetime.datetime):
            pruned_at = self.pruned_at.isoformat()
        else:
            pruned_at = self.pruned_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "app_id": app_id,
                "created_at": created_at,
                "has_source": has_source,
                "id": id,
                "is_live": is_live,
                "preview_url": preview_url,
                "release_number": release_number,
                "version": version,
            }
        )
        if created_by is not UNSET:
            field_dict["created_by"] = created_by
        if label is not UNSET:
            field_dict["label"] = label
        if pruned_at is not UNSET:
            field_dict["pruned_at"] = pruned_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app_id = UUID(d.pop("app_id"))

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("created_at"))

        has_source = d.pop("has_source")

        id = UUID(d.pop("id"))

        is_live = d.pop("is_live")

        preview_url = d.pop("preview_url")

        release_number = d.pop("release_number")

        version = d.pop("version")

        def _parse_created_by(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_type_0 = UUID(data)

                return created_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        created_by = _parse_created_by(d.pop("created_by", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_pruned_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pruned_at_type_0 = isoparse(data)

                return pruned_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        pruned_at = _parse_pruned_at(d.pop("pruned_at", UNSET))

        app_release_response = cls(
            app_id=app_id,
            created_at=created_at,
            has_source=has_source,
            id=id,
            is_live=is_live,
            preview_url=preview_url,
            release_number=release_number,
            version=version,
            created_by=created_by,
            label=label,
            pruned_at=pruned_at,
        )

        app_release_response.additional_properties = d
        return app_release_response

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
