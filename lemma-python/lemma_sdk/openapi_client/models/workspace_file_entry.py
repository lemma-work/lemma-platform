from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workspace_file_entry_kind import WorkspaceFileEntryKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkspaceFileEntry")


@_attrs_define
class WorkspaceFileEntry:
    """
    Attributes:
        kind (WorkspaceFileEntryKind): What this entry is.
        modified_at (datetime.datetime): Last modification time.
        name (str): Final path segment.
        path (str): Absolute path inside the workspace.
        size_bytes (int): Size in bytes; 0 for a directory.
        mode (int | None | Unset): POSIX permission bits, when the fabric reports them. A viewer showing a file it
            cannot write should be able to say so.
        sha256 (None | str | Unset): Content hash, when the fabric computes one. Doubles as the `ETag` on a read, so re-
            opening a file a viewer already has is a 304 rather than the bytes again.
    """

    kind: WorkspaceFileEntryKind
    modified_at: datetime.datetime
    name: str
    path: str
    size_bytes: int
    mode: int | None | Unset = UNSET
    sha256: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        modified_at = self.modified_at.isoformat()

        name = self.name

        path = self.path

        size_bytes = self.size_bytes

        mode: int | None | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        sha256: None | str | Unset
        if isinstance(self.sha256, Unset):
            sha256 = UNSET
        else:
            sha256 = self.sha256

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "modified_at": modified_at,
                "name": name,
                "path": path,
                "size_bytes": size_bytes,
            }
        )
        if mode is not UNSET:
            field_dict["mode"] = mode
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = WorkspaceFileEntryKind(d.pop("kind"))

        modified_at = isoparse(d.pop("modified_at"))

        name = d.pop("name")

        path = d.pop("path")

        size_bytes = d.pop("size_bytes")

        def _parse_mode(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_sha256(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sha256 = _parse_sha256(d.pop("sha256", UNSET))

        workspace_file_entry = cls(
            kind=kind,
            modified_at=modified_at,
            name=name,
            path=path,
            size_bytes=size_bytes,
            mode=mode,
            sha256=sha256,
        )

        workspace_file_entry.additional_properties = d
        return workspace_file_entry

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
