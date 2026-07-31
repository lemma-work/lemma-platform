from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.bundle_source_kind import BundleSourceKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ImportStartRequest")


@_attrs_define
class ImportStartRequest:
    """Body for starting a URL-based import.

    Attributes:
        kind (BundleSourceKind): Where an imported bundle comes from — a CAPS wire enum.

            ``URL`` covers any lemma-origin signed download URL (an export or an
            uploaded ``.zip`` staged into our object storage); ``GITHUB`` is a public
            repo fetched via the connector path.
        account_id (None | Unset | UUID): Connector account for a private GitHub repo.
        owner (None | str | Unset): GITHUB repo owner.
        ref (None | str | Unset): GITHUB branch/tag/sha (optional).
        repo (None | str | Unset): GITHUB repo name.
        url (None | str | Unset): For URL: a lemma bundle download URL (from an export or an upload). For GITHUB: the
            repo URL (alternative to owner+repo).
    """

    kind: BundleSourceKind
    account_id: None | Unset | UUID = UNSET
    owner: None | str | Unset = UNSET
    ref: None | str | Unset = UNSET
    repo: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        owner: None | str | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        else:
            owner = self.owner

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

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if owner is not UNSET:
            field_dict["owner"] = owner
        if ref is not UNSET:
            field_dict["ref"] = ref
        if repo is not UNSET:
            field_dict["repo"] = repo
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = BundleSourceKind(d.pop("kind"))

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_owner(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

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

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        import_start_request = cls(
            kind=kind,
            account_id=account_id,
            owner=owner,
            ref=ref,
            repo=repo,
            url=url,
        )

        import_start_request.additional_properties = d
        return import_start_request

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
