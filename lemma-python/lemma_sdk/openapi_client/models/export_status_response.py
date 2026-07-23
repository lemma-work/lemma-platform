from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.export_status import ExportStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse


T = TypeVar("T", bound="ExportStatusResponse")


@_attrs_define
class ExportStatusResponse:
    """Status of a pod export job (pure Redis read).

    Attributes:
        export_id (UUID):
        status (ExportStatus):
        bundle_filename (None | str | Unset):
        download_url (None | str | Unset): Signed, authenticated download URL; present once the export is READY.
            Requires a logged-in lemma user to fetch.
        error (None | str | Unset):
        expires_at (datetime.datetime | None | Unset): When the download URL (and archive) expires.
        progress (ExportProgressResponse | Unset):
        warnings (list[str] | Unset): Data/asset-cap notices (e.g. truncated seed rows, skipped files).
    """

    export_id: UUID
    status: ExportStatus
    bundle_filename: None | str | Unset = UNSET
    download_url: None | str | Unset = UNSET
    error: None | str | Unset = UNSET
    expires_at: datetime.datetime | None | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        export_id = str(self.export_id)

        status = self.status.value

        bundle_filename: None | str | Unset
        if isinstance(self.bundle_filename, Unset):
            bundle_filename = UNSET
        else:
            bundle_filename = self.bundle_filename

        download_url: None | str | Unset
        if isinstance(self.download_url, Unset):
            download_url = UNSET
        else:
            download_url = self.download_url

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        elif isinstance(self.expires_at, datetime.datetime):
            expires_at = self.expires_at.isoformat()
        else:
            expires_at = self.expires_at

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "export_id": export_id,
                "status": status,
            }
        )
        if bundle_filename is not UNSET:
            field_dict["bundle_filename"] = bundle_filename
        if download_url is not UNSET:
            field_dict["download_url"] = download_url
        if error is not UNSET:
            field_dict["error"] = error
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at
        if progress is not UNSET:
            field_dict["progress"] = progress
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.export_progress_response import ExportProgressResponse

        d = dict(src_dict)
        export_id = UUID(d.pop("export_id"))

        status = ExportStatus(d.pop("status"))

        def _parse_bundle_filename(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        bundle_filename = _parse_bundle_filename(d.pop("bundle_filename", UNSET))

        def _parse_download_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        download_url = _parse_download_url(d.pop("download_url", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_at_type_0 = isoparse(data)

                return expires_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        _progress = d.pop("progress", UNSET)
        progress: ExportProgressResponse | Unset
        if isinstance(_progress, Unset):
            progress = UNSET
        else:
            progress = ExportProgressResponse.from_dict(_progress)

        warnings = cast(list[str], d.pop("warnings", UNSET))

        export_status_response = cls(
            export_id=export_id,
            status=status,
            bundle_filename=bundle_filename,
            download_url=download_url,
            error=error,
            expires_at=expires_at,
            progress=progress,
            warnings=warnings,
        )

        export_status_response.additional_properties = d
        return export_status_response

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
