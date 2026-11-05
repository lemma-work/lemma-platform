from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.contact_status import ContactStatus
from ..models.visibility import Visibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChannelUpdateRequest")


@_attrs_define
class ChannelUpdateRequest:
    """Absent is unchanged; ``null`` clears where clearing means something.

    A group takes ``title``, ``topic``, ``visibility``, ``archived``,
    ``answers_shared``. A contact's channel takes ``agents_answer``, ``hand_back_after_hours``,
    ``status``, ``assignee`` (and needs ``contact.message``). Anything else
    for the kind is 409.

        Attributes:
            agents_answer (bool | None | Unset): On also hands the contact back now: nobody is assigned.
            answers_shared (bool | None | Unset): A group's: on, agents' answers made with the asker's access are read by
                everybody here; off, by the asker alone until they share one. Applies to answers posted from then on.
            archived (bool | None | Unset):
            assignee (Literal['me'] | None | Unset | UUID): `me`, or a member's user id; `null` unassigns.
            hand_back_after_hours (int | None | Unset):
            status (ContactStatus | None | Unset):
            title (None | str | Unset):
            topic (None | str | Unset):
            visibility (None | Unset | Visibility):
    """

    agents_answer: bool | None | Unset = UNSET
    answers_shared: bool | None | Unset = UNSET
    archived: bool | None | Unset = UNSET
    assignee: Literal["me"] | None | Unset | UUID = UNSET
    hand_back_after_hours: int | None | Unset = UNSET
    status: ContactStatus | None | Unset = UNSET
    title: None | str | Unset = UNSET
    topic: None | str | Unset = UNSET
    visibility: None | Unset | Visibility = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents_answer: bool | None | Unset
        if isinstance(self.agents_answer, Unset):
            agents_answer = UNSET
        else:
            agents_answer = self.agents_answer

        answers_shared: bool | None | Unset
        if isinstance(self.answers_shared, Unset):
            answers_shared = UNSET
        else:
            answers_shared = self.answers_shared

        archived: bool | None | Unset
        if isinstance(self.archived, Unset):
            archived = UNSET
        else:
            archived = self.archived

        assignee: Literal["me"] | None | str | Unset
        if isinstance(self.assignee, Unset):
            assignee = UNSET
        elif isinstance(self.assignee, UUID):
            assignee = str(self.assignee)
        else:
            assignee = self.assignee

        hand_back_after_hours: int | None | Unset
        if isinstance(self.hand_back_after_hours, Unset):
            hand_back_after_hours = UNSET
        else:
            hand_back_after_hours = self.hand_back_after_hours

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, ContactStatus):
            status = self.status.value
        else:
            status = self.status

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        topic: None | str | Unset
        if isinstance(self.topic, Unset):
            topic = UNSET
        else:
            topic = self.topic

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, Visibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agents_answer is not UNSET:
            field_dict["agents_answer"] = agents_answer
        if answers_shared is not UNSET:
            field_dict["answers_shared"] = answers_shared
        if archived is not UNSET:
            field_dict["archived"] = archived
        if assignee is not UNSET:
            field_dict["assignee"] = assignee
        if hand_back_after_hours is not UNSET:
            field_dict["hand_back_after_hours"] = hand_back_after_hours
        if status is not UNSET:
            field_dict["status"] = status
        if title is not UNSET:
            field_dict["title"] = title
        if topic is not UNSET:
            field_dict["topic"] = topic
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agents_answer(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        agents_answer = _parse_agents_answer(d.pop("agents_answer", UNSET))

        def _parse_answers_shared(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        answers_shared = _parse_answers_shared(d.pop("answers_shared", UNSET))

        def _parse_archived(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        archived = _parse_archived(d.pop("archived", UNSET))

        def _parse_assignee(data: object) -> Literal["me"] | None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assignee_type_0 = UUID(data)

                return assignee_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            assignee_type_1 = cast(Literal["me"], data)
            if assignee_type_1 != "me":
                raise ValueError(
                    f"assignee_type_1 must match const 'me', got '{assignee_type_1}'"
                )
            return assignee_type_1
            return cast(Literal["me"] | None | Unset | UUID, data)

        assignee = _parse_assignee(d.pop("assignee", UNSET))

        def _parse_hand_back_after_hours(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        hand_back_after_hours = _parse_hand_back_after_hours(
            d.pop("hand_back_after_hours", UNSET)
        )

        def _parse_status(data: object) -> ContactStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = ContactStatus(data)

                return status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ContactStatus | None | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_topic(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        topic = _parse_topic(d.pop("topic", UNSET))

        def _parse_visibility(data: object) -> None | Unset | Visibility:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = Visibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | Visibility, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        channel_update_request = cls(
            agents_answer=agents_answer,
            answers_shared=answers_shared,
            archived=archived,
            assignee=assignee,
            hand_back_after_hours=hand_back_after_hours,
            status=status,
            title=title,
            topic=topic,
            visibility=visibility,
        )

        channel_update_request.additional_properties = d
        return channel_update_request

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
