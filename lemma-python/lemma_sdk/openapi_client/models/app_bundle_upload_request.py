from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, File, FileTypes, Unset

T = TypeVar("T", bound="AppBundleUploadRequest")


@_attrs_define
class AppBundleUploadRequest:
    """
    Attributes:
        dist_archive (File | None | Unset):
        source_archive (File | None | Unset):
    """

    dist_archive: File | None | Unset = UNSET
    source_archive: File | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dist_archive: FileTypes | None | Unset
        if isinstance(self.dist_archive, Unset):
            dist_archive = UNSET
        elif isinstance(self.dist_archive, File):
            dist_archive = self.dist_archive.to_tuple()

        else:
            dist_archive = self.dist_archive

        source_archive: FileTypes | None | Unset
        if isinstance(self.source_archive, Unset):
            source_archive = UNSET
        elif isinstance(self.source_archive, File):
            source_archive = self.source_archive.to_tuple()

        else:
            source_archive = self.source_archive

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if dist_archive is not UNSET:
            field_dict["dist_archive"] = dist_archive
        if source_archive is not UNSET:
            field_dict["source_archive"] = source_archive

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        if not isinstance(self.dist_archive, Unset):
            if isinstance(self.dist_archive, File):
                files.append(("dist_archive", self.dist_archive.to_tuple()))
            else:
                files.append(
                    (
                        "dist_archive",
                        (None, str(self.dist_archive).encode(), "text/plain"),
                    )
                )

        if not isinstance(self.source_archive, Unset):
            if isinstance(self.source_archive, File):
                files.append(("source_archive", self.source_archive.to_tuple()))
            else:
                files.append(
                    (
                        "source_archive",
                        (None, str(self.source_archive).encode(), "text/plain"),
                    )
                )

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_dist_archive(data: object) -> File | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, bytes):
                    raise TypeError()
                dist_archive_type_0 = File(payload=BytesIO(data))

                return dist_archive_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(File | None | Unset, data)

        dist_archive = _parse_dist_archive(d.pop("dist_archive", UNSET))

        def _parse_source_archive(data: object) -> File | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, bytes):
                    raise TypeError()
                source_archive_type_0 = File(payload=BytesIO(data))

                return source_archive_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(File | None | Unset, data)

        source_archive = _parse_source_archive(d.pop("source_archive", UNSET))

        app_bundle_upload_request = cls(
            dist_archive=dist_archive,
            source_archive=source_archive,
        )

        app_bundle_upload_request.additional_properties = d
        return app_bundle_upload_request

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
