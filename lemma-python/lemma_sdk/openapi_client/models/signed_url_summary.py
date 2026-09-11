from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="SignedUrlSummary")


@_attrs_define
class SignedUrlSummary:
    """One live public link, as its pod sees it.

    Deliberately carries the ``code`` and not the full URL: this is the listing
    a pod member reads to decide what to revoke, and the code is what revoking
    takes. Anyone who needs the openable URL already has it.

        Attributes:
            code (str):
            content_type (str):
            expires_at (datetime.datetime):
            filename (str):
            max_hits (int):
            path (str):
            size_bytes (int):
            created_at (datetime.datetime | None | Unset):
            exhausted_at (datetime.datetime | None | Unset):
            revoked_at (datetime.datetime | None | Unset):
    """

    code: str
    content_type: str
    expires_at: datetime.datetime
    filename: str
    max_hits: int
    path: str
    size_bytes: int
    created_at: datetime.datetime | None | Unset = UNSET
    exhausted_at: datetime.datetime | None | Unset = UNSET
    revoked_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        content_type = self.content_type

        expires_at = self.expires_at.isoformat()

        filename = self.filename

        max_hits = self.max_hits

        path = self.path

        size_bytes = self.size_bytes

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        exhausted_at: None | str | Unset
        if isinstance(self.exhausted_at, Unset):
            exhausted_at = UNSET
        elif isinstance(self.exhausted_at, datetime.datetime):
            exhausted_at = self.exhausted_at.isoformat()
        else:
            exhausted_at = self.exhausted_at

        revoked_at: None | str | Unset
        if isinstance(self.revoked_at, Unset):
            revoked_at = UNSET
        elif isinstance(self.revoked_at, datetime.datetime):
            revoked_at = self.revoked_at.isoformat()
        else:
            revoked_at = self.revoked_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "content_type": content_type,
                "expires_at": expires_at,
                "filename": filename,
                "max_hits": max_hits,
                "path": path,
                "size_bytes": size_bytes,
            }
        )
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if exhausted_at is not UNSET:
            field_dict["exhausted_at"] = exhausted_at
        if revoked_at is not UNSET:
            field_dict["revoked_at"] = revoked_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        code = d.pop("code")

        content_type = d.pop("content_type")

        expires_at = isoparse(d.pop("expires_at"))

        filename = d.pop("filename")

        max_hits = d.pop("max_hits")

        path = d.pop("path")

        size_bytes = d.pop("size_bytes")

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_exhausted_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                exhausted_at_type_0 = isoparse(data)

                return exhausted_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        exhausted_at = _parse_exhausted_at(d.pop("exhausted_at", UNSET))

        def _parse_revoked_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                revoked_at_type_0 = isoparse(data)

                return revoked_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        revoked_at = _parse_revoked_at(d.pop("revoked_at", UNSET))

        signed_url_summary = cls(
            code=code,
            content_type=content_type,
            expires_at=expires_at,
            filename=filename,
            max_hits=max_hits,
            path=path,
            size_bytes=size_bytes,
            created_at=created_at,
            exhausted_at=exhausted_at,
            revoked_at=revoked_at,
        )

        signed_url_summary.additional_properties = d
        return signed_url_summary

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
