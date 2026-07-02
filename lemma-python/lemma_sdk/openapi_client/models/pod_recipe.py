from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="PodRecipe")


@_attrs_define
class PodRecipe:
    """A record of a bundle installed into this pod (the durable trace of an
    import; the ephemeral import job state is not kept). ``kind`` distinguishes an
    uploaded bundle from a GitHub-sourced one; ``repo_url`` is set for GitHub.

        Attributes:
            imported_at (datetime.datetime):
            imported_by (UUID):
            kind (str):
            format_version (int | None | Unset):
            name (None | str | Unset):
            repo_url (None | str | Unset):
    """

    imported_at: datetime.datetime
    imported_by: UUID
    kind: str
    format_version: int | None | Unset = UNSET
    name: None | str | Unset = UNSET
    repo_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        imported_at = self.imported_at.isoformat()

        imported_by = str(self.imported_by)

        kind = self.kind

        format_version: int | None | Unset
        if isinstance(self.format_version, Unset):
            format_version = UNSET
        else:
            format_version = self.format_version

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        repo_url: None | str | Unset
        if isinstance(self.repo_url, Unset):
            repo_url = UNSET
        else:
            repo_url = self.repo_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "imported_at": imported_at,
                "imported_by": imported_by,
                "kind": kind,
            }
        )
        if format_version is not UNSET:
            field_dict["format_version"] = format_version
        if name is not UNSET:
            field_dict["name"] = name
        if repo_url is not UNSET:
            field_dict["repo_url"] = repo_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        imported_at = isoparse(d.pop("imported_at"))

        imported_by = UUID(d.pop("imported_by"))

        kind = d.pop("kind")

        def _parse_format_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        format_version = _parse_format_version(d.pop("format_version", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_repo_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repo_url = _parse_repo_url(d.pop("repo_url", UNSET))

        pod_recipe = cls(
            imported_at=imported_at,
            imported_by=imported_by,
            kind=kind,
            format_version=format_version,
            name=name,
            repo_url=repo_url,
        )

        pod_recipe.additional_properties = d
        return pod_recipe

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
