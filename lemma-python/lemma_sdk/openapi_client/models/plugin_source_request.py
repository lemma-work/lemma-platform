from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="PluginSourceRequest")


@_attrs_define
class PluginSourceRequest:
    """One plugin: a GitHub repository, and the folder in it its bundle is in.

    Attributes:
        owner (None | str | Unset): Repository owner.
        path (None | str | Unset): The folder that holds the bundle's `pod.json`, e.g. 'install/mail'. Omit for a
            repository that is one bundle.
        ref (None | str | Unset): Branch, tag or sha.
        repo (None | str | Unset): Repository name.
        repo_url (None | str | Unset): The repository's URL (alternative to owner+repo).
    """

    owner: None | str | Unset = UNSET
    path: None | str | Unset = UNSET
    ref: None | str | Unset = UNSET
    repo: None | str | Unset = UNSET
    repo_url: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        owner: None | str | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        else:
            owner = self.owner

        path: None | str | Unset
        if isinstance(self.path, Unset):
            path = UNSET
        else:
            path = self.path

        ref: None | str | Unset
        if isinstance(self.ref, Unset):
            ref = UNSET
        else:
            ref = self.ref

        repo: None | str | Unset
        if isinstance(self.repo, Unset):
            repo = UNSET
        else:
            repo = self.repo

        repo_url: None | str | Unset
        if isinstance(self.repo_url, Unset):
            repo_url = UNSET
        else:
            repo_url = self.repo_url

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if owner is not UNSET:
            field_dict["owner"] = owner
        if path is not UNSET:
            field_dict["path"] = path
        if ref is not UNSET:
            field_dict["ref"] = ref
        if repo is not UNSET:
            field_dict["repo"] = repo
        if repo_url is not UNSET:
            field_dict["repo_url"] = repo_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_owner(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

        def _parse_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        path = _parse_path(d.pop("path", UNSET))

        def _parse_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ref = _parse_ref(d.pop("ref", UNSET))

        def _parse_repo(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repo = _parse_repo(d.pop("repo", UNSET))

        def _parse_repo_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repo_url = _parse_repo_url(d.pop("repo_url", UNSET))

        plugin_source_request = cls(
            owner=owner,
            path=path,
            ref=ref,
            repo=repo,
            repo_url=repo_url,
        )

        return plugin_source_request
