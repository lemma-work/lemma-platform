from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishStartRequest")


@_attrs_define
class PublishStartRequest:
    """Body for publishing a pod to GitHub.

    Attributes:
        repo_name (str): Name for the new GitHub repo.
        account_id (None | Unset | UUID): GitHub connector account to publish as (optional).
        ai_readme (bool | Unset): Polish the generated README with the system model. Default: False.
        private (bool | Unset): Create the repo as private. Default: False.
    """

    repo_name: str
    account_id: None | Unset | UUID = UNSET
    ai_readme: bool | Unset = False
    private: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        repo_name = self.repo_name

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        ai_readme = self.ai_readme

        private = self.private

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "repo_name": repo_name,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if ai_readme is not UNSET:
            field_dict["ai_readme"] = ai_readme
        if private is not UNSET:
            field_dict["private"] = private

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        repo_name = d.pop("repo_name")

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
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        ai_readme = d.pop("ai_readme", UNSET)

        private = d.pop("private", UNSET)

        publish_start_request = cls(
            repo_name=repo_name,
            account_id=account_id,
            ai_readme=ai_readme,
            private=private,
        )

        publish_start_request.additional_properties = d
        return publish_start_request

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
