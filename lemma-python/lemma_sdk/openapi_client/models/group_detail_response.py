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
    from ..models.group_owner_response import GroupOwnerResponse
    from ..models.group_person_response import GroupPersonResponse
    from ..models.group_public_response import GroupPublicResponse
    from ..models.group_waiting_response import GroupWaitingResponse


T = TypeVar("T", bound="GroupDetailResponse")


@_attrs_define
class GroupDetailResponse:
    """
    Attributes:
        answers_outsiders (bool):
        id (UUID):
        people (list[GroupPersonResponse]):
        platform (str):
        surface_name (str):
        updated_at (datetime.datetime):
        waiting (list[GroupWaitingResponse]):
        welcomes_outsiders (bool): People outside the pod are answered here today: the group's switch is on, a member of
            the pod answers for them, and the bot's own switch is on.
        bot_answers_outsiders (bool | Unset): The bot's own switch, over every group it is in. Off, nobody outside the
            pod is answered in any of them. Default: True.
        can_manage (bool | Unset): The reader may switch outsiders for this group or take it over: they answer for it,
            nobody in the pod does, or they are an admin of the pod. Default: False.
        external_channel_id (None | str | Unset):
        invite_link (None | str | Unset):
        last_message_at (datetime.datetime | None | Unset):
        owner (GroupOwnerResponse | None | Unset): Who answers for the people outside the pod.
        pending (bool | Unset): Asked of the platform and not yet confirmed (WhatsApp). Default: False.
        people_in_pod (int | None | Unset): People in the pod seen speaking here; none where not kept.
        people_outside (int | None | Unset): People outside the pod seen speaking here.
        public (GroupPublicResponse | None | Unset):
        shared_externally (bool | Unset): A Slack channel shared with another company. Default: False.
        title (None | str | Unset):
        waiting_for_you (int | Unset): Questions its people outside the pod passed on to you. Default: 0.
    """

    answers_outsiders: bool
    id: UUID
    people: list[GroupPersonResponse]
    platform: str
    surface_name: str
    updated_at: datetime.datetime
    waiting: list[GroupWaitingResponse]
    welcomes_outsiders: bool
    bot_answers_outsiders: bool | Unset = True
    can_manage: bool | Unset = False
    external_channel_id: None | str | Unset = UNSET
    invite_link: None | str | Unset = UNSET
    last_message_at: datetime.datetime | None | Unset = UNSET
    owner: GroupOwnerResponse | None | Unset = UNSET
    pending: bool | Unset = False
    people_in_pod: int | None | Unset = UNSET
    people_outside: int | None | Unset = UNSET
    public: GroupPublicResponse | None | Unset = UNSET
    shared_externally: bool | Unset = False
    title: None | str | Unset = UNSET
    waiting_for_you: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.group_owner_response import GroupOwnerResponse
        from ..models.group_public_response import GroupPublicResponse

        answers_outsiders = self.answers_outsiders

        id = str(self.id)

        people = []
        for people_item_data in self.people:
            people_item = people_item_data.to_dict()
            people.append(people_item)

        platform = self.platform

        surface_name = self.surface_name

        updated_at = self.updated_at.isoformat()

        waiting = []
        for waiting_item_data in self.waiting:
            waiting_item = waiting_item_data.to_dict()
            waiting.append(waiting_item)

        welcomes_outsiders = self.welcomes_outsiders

        bot_answers_outsiders = self.bot_answers_outsiders

        can_manage = self.can_manage

        external_channel_id: None | str | Unset
        if isinstance(self.external_channel_id, Unset):
            external_channel_id = UNSET
        else:
            external_channel_id = self.external_channel_id

        invite_link: None | str | Unset
        if isinstance(self.invite_link, Unset):
            invite_link = UNSET
        else:
            invite_link = self.invite_link

        last_message_at: None | str | Unset
        if isinstance(self.last_message_at, Unset):
            last_message_at = UNSET
        elif isinstance(self.last_message_at, datetime.datetime):
            last_message_at = self.last_message_at.isoformat()
        else:
            last_message_at = self.last_message_at

        owner: dict[str, Any] | None | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        elif isinstance(self.owner, GroupOwnerResponse):
            owner = self.owner.to_dict()
        else:
            owner = self.owner

        pending = self.pending

        people_in_pod: int | None | Unset
        if isinstance(self.people_in_pod, Unset):
            people_in_pod = UNSET
        else:
            people_in_pod = self.people_in_pod

        people_outside: int | None | Unset
        if isinstance(self.people_outside, Unset):
            people_outside = UNSET
        else:
            people_outside = self.people_outside

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

        waiting_for_you = self.waiting_for_you

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answers_outsiders": answers_outsiders,
                "id": id,
                "people": people,
                "platform": platform,
                "surface_name": surface_name,
                "updated_at": updated_at,
                "waiting": waiting,
                "welcomes_outsiders": welcomes_outsiders,
            }
        )
        if bot_answers_outsiders is not UNSET:
            field_dict["bot_answers_outsiders"] = bot_answers_outsiders
        if can_manage is not UNSET:
            field_dict["can_manage"] = can_manage
        if external_channel_id is not UNSET:
            field_dict["external_channel_id"] = external_channel_id
        if invite_link is not UNSET:
            field_dict["invite_link"] = invite_link
        if last_message_at is not UNSET:
            field_dict["last_message_at"] = last_message_at
        if owner is not UNSET:
            field_dict["owner"] = owner
        if pending is not UNSET:
            field_dict["pending"] = pending
        if people_in_pod is not UNSET:
            field_dict["people_in_pod"] = people_in_pod
        if people_outside is not UNSET:
            field_dict["people_outside"] = people_outside
        if public is not UNSET:
            field_dict["public"] = public
        if shared_externally is not UNSET:
            field_dict["shared_externally"] = shared_externally
        if title is not UNSET:
            field_dict["title"] = title
        if waiting_for_you is not UNSET:
            field_dict["waiting_for_you"] = waiting_for_you

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.group_owner_response import GroupOwnerResponse
        from ..models.group_person_response import GroupPersonResponse
        from ..models.group_public_response import GroupPublicResponse
        from ..models.group_waiting_response import GroupWaitingResponse

        d = dict(src_dict)
        answers_outsiders = d.pop("answers_outsiders")

        id = UUID(d.pop("id"))

        people = []
        _people = d.pop("people")
        for people_item_data in _people:
            people_item = GroupPersonResponse.from_dict(people_item_data)

            people.append(people_item)

        platform = d.pop("platform")

        surface_name = d.pop("surface_name")

        updated_at = isoparse(d.pop("updated_at"))

        waiting = []
        _waiting = d.pop("waiting")
        for waiting_item_data in _waiting:
            waiting_item = GroupWaitingResponse.from_dict(waiting_item_data)

            waiting.append(waiting_item)

        welcomes_outsiders = d.pop("welcomes_outsiders")

        bot_answers_outsiders = d.pop("bot_answers_outsiders", UNSET)

        can_manage = d.pop("can_manage", UNSET)

        def _parse_external_channel_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        external_channel_id = _parse_external_channel_id(
            d.pop("external_channel_id", UNSET)
        )

        def _parse_invite_link(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        invite_link = _parse_invite_link(d.pop("invite_link", UNSET))

        def _parse_last_message_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_message_at_type_0 = isoparse(data)

                return last_message_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_message_at = _parse_last_message_at(d.pop("last_message_at", UNSET))

        def _parse_owner(data: object) -> GroupOwnerResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                owner_type_0 = GroupOwnerResponse.from_dict(data)

                return owner_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(GroupOwnerResponse | None | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

        pending = d.pop("pending", UNSET)

        def _parse_people_in_pod(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        people_in_pod = _parse_people_in_pod(d.pop("people_in_pod", UNSET))

        def _parse_people_outside(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        people_outside = _parse_people_outside(d.pop("people_outside", UNSET))

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

        waiting_for_you = d.pop("waiting_for_you", UNSET)

        group_detail_response = cls(
            answers_outsiders=answers_outsiders,
            id=id,
            people=people,
            platform=platform,
            surface_name=surface_name,
            updated_at=updated_at,
            waiting=waiting,
            welcomes_outsiders=welcomes_outsiders,
            bot_answers_outsiders=bot_answers_outsiders,
            can_manage=can_manage,
            external_channel_id=external_channel_id,
            invite_link=invite_link,
            last_message_at=last_message_at,
            owner=owner,
            pending=pending,
            people_in_pod=people_in_pod,
            people_outside=people_outside,
            public=public,
            shared_externally=shared_externally,
            title=title,
            waiting_for_you=waiting_for_you,
        )

        group_detail_response.additional_properties = d
        return group_detail_response

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
