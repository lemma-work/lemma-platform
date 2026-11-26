from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.conversation_changes_response_state import (
    ConversationChangesResponseState,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.conversation_changed_file import ConversationChangedFile
    from ..models.conversation_changes_totals import ConversationChangesTotals


T = TypeVar("T", bound="ConversationChangesResponse")


@_attrs_define
class ConversationChangesResponse:
    """What the conversation's checkout holds that its start point does not.

    Attributes:
        state (ConversationChangesResponseState): `ready`: read now, and everything below is filled in. `no_repository`:
            the conversation was not opened in a repository. `not_checked_out`: it was, and its machine answered, but the
            conversation's directory holds no checkout -- cleared, or never made on this machine. `machine_unavailable`: the
            machine it works on is not running -- it is not started to answer, so a conversation with no run yet answers
            this -- or a run of the conversation may not use it now. `elsewhere`: its last run worked on its person's own
            Mac (a coding agent through Agent Host, or host execution), where the checkout is.
        ahead (int | Unset): Commits on the checkout since `base_sha`. Default: 0.
        base_ref (None | str | Unset): The start point compared against: `origin/<ref>`, the ref itself for a tag or a
            commit, or the branch `origin/HEAD` names (as `origin/main`). Null when it could not be found in the checkout;
            the files are then its uncommitted work only.
        base_sha (None | str | Unset): The merge base of the checkout's HEAD with `base_ref`.
        branch (None | str | Unset): The checkout's branch; null when detached.
        files (list[ConversationChangedFile] | Unset): Every file that differs from `base_sha` in the working tree --
            committed, staged or not -- then the untracked files.
        head_sha (None | str | Unset):
        patch (None | str | Unset): The unified diff, when asked for with `patch=true`.
        totals (ConversationChangesTotals | Unset):
        truncated (bool | Unset): Something was cut to fit: the patch at `max_patch_bytes`, or the file list in a very
            large change. Default: False.
        uncommitted (bool | Unset): Edits not yet committed, or untracked files, are present. Default: False.
    """

    state: ConversationChangesResponseState
    ahead: int | Unset = 0
    base_ref: None | str | Unset = UNSET
    base_sha: None | str | Unset = UNSET
    branch: None | str | Unset = UNSET
    files: list[ConversationChangedFile] | Unset = UNSET
    head_sha: None | str | Unset = UNSET
    patch: None | str | Unset = UNSET
    totals: ConversationChangesTotals | Unset = UNSET
    truncated: bool | Unset = False
    uncommitted: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        ahead = self.ahead

        base_ref: None | str | Unset
        if isinstance(self.base_ref, Unset):
            base_ref = UNSET
        else:
            base_ref = self.base_ref

        base_sha: None | str | Unset
        if isinstance(self.base_sha, Unset):
            base_sha = UNSET
        else:
            base_sha = self.base_sha

        branch: None | str | Unset
        if isinstance(self.branch, Unset):
            branch = UNSET
        else:
            branch = self.branch

        files: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.files, Unset):
            files = []
            for files_item_data in self.files:
                files_item = files_item_data.to_dict()
                files.append(files_item)

        head_sha: None | str | Unset
        if isinstance(self.head_sha, Unset):
            head_sha = UNSET
        else:
            head_sha = self.head_sha

        patch: None | str | Unset
        if isinstance(self.patch, Unset):
            patch = UNSET
        else:
            patch = self.patch

        totals: dict[str, Any] | Unset = UNSET
        if not isinstance(self.totals, Unset):
            totals = self.totals.to_dict()

        truncated = self.truncated

        uncommitted = self.uncommitted

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if ahead is not UNSET:
            field_dict["ahead"] = ahead
        if base_ref is not UNSET:
            field_dict["base_ref"] = base_ref
        if base_sha is not UNSET:
            field_dict["base_sha"] = base_sha
        if branch is not UNSET:
            field_dict["branch"] = branch
        if files is not UNSET:
            field_dict["files"] = files
        if head_sha is not UNSET:
            field_dict["head_sha"] = head_sha
        if patch is not UNSET:
            field_dict["patch"] = patch
        if totals is not UNSET:
            field_dict["totals"] = totals
        if truncated is not UNSET:
            field_dict["truncated"] = truncated
        if uncommitted is not UNSET:
            field_dict["uncommitted"] = uncommitted

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.conversation_changed_file import ConversationChangedFile
        from ..models.conversation_changes_totals import ConversationChangesTotals

        d = dict(src_dict)
        state = ConversationChangesResponseState(d.pop("state"))

        ahead = d.pop("ahead", UNSET)

        def _parse_base_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_ref = _parse_base_ref(d.pop("base_ref", UNSET))

        def _parse_base_sha(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_sha = _parse_base_sha(d.pop("base_sha", UNSET))

        def _parse_branch(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        branch = _parse_branch(d.pop("branch", UNSET))

        _files = d.pop("files", UNSET)
        files: list[ConversationChangedFile] | Unset = UNSET
        if _files is not UNSET:
            files = []
            for files_item_data in _files:
                files_item = ConversationChangedFile.from_dict(files_item_data)

                files.append(files_item)

        def _parse_head_sha(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        head_sha = _parse_head_sha(d.pop("head_sha", UNSET))

        def _parse_patch(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        patch = _parse_patch(d.pop("patch", UNSET))

        _totals = d.pop("totals", UNSET)
        totals: ConversationChangesTotals | Unset
        if isinstance(_totals, Unset):
            totals = UNSET
        else:
            totals = ConversationChangesTotals.from_dict(_totals)

        truncated = d.pop("truncated", UNSET)

        uncommitted = d.pop("uncommitted", UNSET)

        conversation_changes_response = cls(
            state=state,
            ahead=ahead,
            base_ref=base_ref,
            base_sha=base_sha,
            branch=branch,
            files=files,
            head_sha=head_sha,
            patch=patch,
            totals=totals,
            truncated=truncated,
            uncommitted=uncommitted,
        )

        conversation_changes_response.additional_properties = d
        return conversation_changes_response

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
