from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define

from ..models.publish_mode import PublishMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishStartRequest")


@_attrs_define
class PublishStartRequest:
    """Body for publishing a pod to GitHub.

    Attributes:
        repo_name (str): GitHub repository to publish into, as `name` or `owner/name`. It must already exist and be
            covered by the Lemma app's installation -- publishing does not create repositories, because a GitHub App cannot.
        account_id (None | Unset | UUID): Deprecated and ignored: the publish runs as the account the pod's `github`
            connector resolves to for you.
        ai_readme (bool | Unset): Polish the generated README with the system model. Default: False.
        mode (PublishMode | Unset):
        private (bool | Unset): Recorded on the job and reported back, but not acted on: the repository already exists,
            so its visibility is whatever it was created with. Default: False.
    """

    repo_name: str
    account_id: None | Unset | UUID = UNSET
    ai_readme: bool | Unset = False
    mode: PublishMode | Unset = UNSET
    private: bool | Unset = False

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

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        private = self.private

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "repo_name": repo_name,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if ai_readme is not UNSET:
            field_dict["ai_readme"] = ai_readme
        if mode is not UNSET:
            field_dict["mode"] = mode
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
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        ai_readme = d.pop("ai_readme", UNSET)

        _mode = d.pop("mode", UNSET)
        mode: PublishMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = PublishMode(_mode)

        private = d.pop("private", UNSET)

        publish_start_request = cls(
            repo_name=repo_name,
            account_id=account_id,
            ai_readme=ai_readme,
            mode=mode,
            private=private,
        )

        return publish_start_request
