from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="FirstWorkspaceRequest")


@_attrs_define
class FirstWorkspaceRequest:
    """
    Attributes:
        own_organization (bool | Unset): Skip their company's organization, even when it is open to their work domain,
            and make a workspace of their own. It is made by invitation only and named after the person. Invitations and an
            organization they already belong to still come first. Default: False.
        team (bool | None | Unset): What kind of organization to make when one is made. Omitted: a company's, named
            after it and open to colleagues from the same domain, when the person's address is verified and on a work
            domain; otherwise a workspace of one. false: a workspace of one whatever the address, for a deployment nobody
            else can reach.
        with_pod (bool | Unset): Make a private pod and assistant when the person would otherwise land in no Lemma: an
            organization that has no Lemma of its own, or one whose Lemma they left. A new organization is always made with
            one. Default: True.
    """

    own_organization: bool | Unset = False
    team: bool | None | Unset = UNSET
    with_pod: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        own_organization = self.own_organization

        team: bool | None | Unset
        if isinstance(self.team, Unset):
            team = UNSET
        else:
            team = self.team

        with_pod = self.with_pod

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if own_organization is not UNSET:
            field_dict["own_organization"] = own_organization
        if team is not UNSET:
            field_dict["team"] = team
        if with_pod is not UNSET:
            field_dict["with_pod"] = with_pod

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        own_organization = d.pop("own_organization", UNSET)

        def _parse_team(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        team = _parse_team(d.pop("team", UNSET))

        with_pod = d.pop("with_pod", UNSET)

        first_workspace_request = cls(
            own_organization=own_organization,
            team=team,
            with_pod=with_pod,
        )

        return first_workspace_request
