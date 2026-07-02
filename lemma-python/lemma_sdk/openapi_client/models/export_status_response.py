from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

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
        download_url (None | str | Unset): Relative download path; present once the export is READY.
        error (None | str | Unset):
        progress (ExportProgressResponse | Unset):
    """

    export_id: UUID
    status: ExportStatus
    bundle_filename: None | str | Unset = UNSET
    download_url: None | str | Unset = UNSET
    error: None | str | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
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

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

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
        if progress is not UNSET:
            field_dict["progress"] = progress

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

        _progress = d.pop("progress", UNSET)
        progress: ExportProgressResponse | Unset
        if isinstance(_progress, Unset):
            progress = UNSET
        else:
            progress = ExportProgressResponse.from_dict(_progress)

        export_status_response = cls(
            export_id=export_id,
            status=status,
            bundle_filename=bundle_filename,
            download_url=download_url,
            error=error,
            progress=progress,
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
