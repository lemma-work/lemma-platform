from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.group_public_response import GroupPublicResponse
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="SourceSettingsDetailResponse")


@_attrs_define
class SourceSettingsDetailResponse:
    """
    Attributes:
        answers_outsiders (bool): This group's own switch for people outside the pod.
        channel_id (UUID):
        id (UUID): The source's id, as the channel lists it.
        platform (str):
        surface_name (str):
        updated_at (datetime.datetime):
        welcomes_outsiders (bool): People outside the pod are answered here today: the group's switch is on, a member of
            the pod answers for them, and the bot's own switch is on.
        bot_answers_outsiders (bool | Unset): The bot's own switch, over every group it is in. Off, nobody outside the
            pod is answered in any of them. Default: True.
        can_manage (bool | Unset): The reader may switch outsiders here or take it over: they answer for it, nobody in
            the pod does, or they are an admin of the pod. Default: False.
        invite_link (None | str | Unset):
        owner (None | PersonRef | Unset): The member who answers for its outsiders.
        pending (bool | Unset): Asked of the platform and not yet confirmed (WhatsApp). Default: False.
        public (GroupPublicResponse | None | Unset): What people outside the pod can be answered from.
        shared_externally (bool | Unset): A Slack channel shared with another company. Default: False.
        title (None | str | Unset):
    """

    answers_outsiders: bool
    channel_id: UUID
    id: UUID
    platform: str
    surface_name: str
    updated_at: datetime.datetime
    welcomes_outsiders: bool
    bot_answers_outsiders: bool | Unset = True
    can_manage: bool | Unset = False
    invite_link: None | str | Unset = UNSET
    owner: None | PersonRef | Unset = UNSET
    pending: bool | Unset = False
    public: GroupPublicResponse | None | Unset = UNSET
    shared_externally: bool | Unset = False
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.group_public_response import GroupPublicResponse
        from ..models.person_ref import PersonRef

        answers_outsiders = self.answers_outsiders

        channel_id = str(self.channel_id)

        id = str(self.id)

        platform = self.platform

        surface_name = self.surface_name

        updated_at = self.updated_at.isoformat()

        welcomes_outsiders = self.welcomes_outsiders

        bot_answers_outsiders = self.bot_answers_outsiders

        can_manage = self.can_manage

        invite_link: None | str | Unset
        if isinstance(self.invite_link, Unset):
            invite_link = UNSET
        else:
            invite_link = self.invite_link

        owner: dict[str, Any] | None | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        elif isinstance(self.owner, PersonRef):
            owner = self.owner.to_dict()
        else:
            owner = self.owner

        pending = self.pending

        public: dict[str, Any] | None | Unset
        if isinstance(self.public, Unset):
            public = UNSET
        elif isinstance(self.public, GroupPublicResponse):
            public = self.public.to_dict()
        else:
            public = self.public

        shared_externally = self.shared_externally

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answers_outsiders": answers_outsiders,
                "channel_id": channel_id,
                "id": id,
                "platform": platform,
                "surface_name": surface_name,
                "updated_at": updated_at,
                "welcomes_outsiders": welcomes_outsiders,
            }
        )
        if bot_answers_outsiders is not UNSET:
            field_dict["bot_answers_outsiders"] = bot_answers_outsiders
        if can_manage is not UNSET:
            field_dict["can_manage"] = can_manage
        if invite_link is not UNSET:
            field_dict["invite_link"] = invite_link
        if owner is not UNSET:
            field_dict["owner"] = owner
        if pending is not UNSET:
            field_dict["pending"] = pending
        if public is not UNSET:
            field_dict["public"] = public
        if shared_externally is not UNSET:
            field_dict["shared_externally"] = shared_externally
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.group_public_response import GroupPublicResponse
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        answers_outsiders = d.pop("answers_outsiders")

        channel_id = UUID(d.pop("channel_id"))

        id = UUID(d.pop("id"))

        platform = d.pop("platform")

        surface_name = d.pop("surface_name")

        updated_at = isoparse(d.pop("updated_at"))

        welcomes_outsiders = d.pop("welcomes_outsiders")

        bot_answers_outsiders = d.pop("bot_answers_outsiders", UNSET)

        can_manage = d.pop("can_manage", UNSET)

        def _parse_invite_link(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        invite_link = _parse_invite_link(d.pop("invite_link", UNSET))

        def _parse_owner(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                owner_type_0 = PersonRef.from_dict(data)

                return owner_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

        pending = d.pop("pending", UNSET)

        def _parse_public(data: object) -> GroupPublicResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                public_type_0 = GroupPublicResponse.from_dict(data)

                return public_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(GroupPublicResponse | None | Unset, data)

        public = _parse_public(d.pop("public", UNSET))

        shared_externally = d.pop("shared_externally", UNSET)

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        source_settings_detail_response = cls(
            answers_outsiders=answers_outsiders,
            channel_id=channel_id,
            id=id,
            platform=platform,
            surface_name=surface_name,
            updated_at=updated_at,
            welcomes_outsiders=welcomes_outsiders,
            bot_answers_outsiders=bot_answers_outsiders,
            can_manage=can_manage,
            invite_link=invite_link,
            owner=owner,
            pending=pending,
            public=public,
            shared_externally=shared_externally,
            title=title,
        )

        source_settings_detail_response.additional_properties = d
        return source_settings_detail_response

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
