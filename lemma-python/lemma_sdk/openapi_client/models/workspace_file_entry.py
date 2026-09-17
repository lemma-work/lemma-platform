from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workspace_file_entry_kind import WorkspaceFileEntryKind

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
    """

    kind: WorkspaceFileEntryKind
    modified_at: datetime.datetime
    name: str
    path: str
    size_bytes: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        modified_at = self.modified_at.isoformat()

        name = self.name

        path = self.path

        size_bytes = self.size_bytes

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

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = WorkspaceFileEntryKind(d.pop("kind"))

        modified_at = isoparse(d.pop("modified_at"))

        name = d.pop("name")

        path = d.pop("path")

        size_bytes = d.pop("size_bytes")

        workspace_file_entry = cls(
            kind=kind,
            modified_at=modified_at,
            name=name,
            path=path,
            size_bytes=size_bytes,
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
