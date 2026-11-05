from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_answers import AgentAnswers
from ..models.member_role import MemberRole
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="ChannelMemberResponse")


@_attrs_define
class ChannelMemberResponse:
    """
    Attributes:
        person (PersonRef): Somebody in a channel, said one way everywhere: a member of the pod, one
            of its contacts, or one of its agents. There is nobody else -- everybody
            outside the pod is a contact.
        role (MemberRole):
        answers (AgentAnswers | None | Unset): An agent's: when it answers here.
        joined_at (datetime.datetime | None | Unset):
    """

    person: PersonRef
    role: MemberRole
    answers: AgentAnswers | None | Unset = UNSET
    joined_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        person = self.person.to_dict()

        role = self.role.value

        answers: None | str | Unset
        if isinstance(self.answers, Unset):
            answers = UNSET
        elif isinstance(self.answers, AgentAnswers):
            answers = self.answers.value
        else:
            answers = self.answers

        joined_at: None | str | Unset
        if isinstance(self.joined_at, Unset):
            joined_at = UNSET
        elif isinstance(self.joined_at, datetime.datetime):
            joined_at = self.joined_at.isoformat()
        else:
            joined_at = self.joined_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "person": person,
                "role": role,
            }
        )
        if answers is not UNSET:
            field_dict["answers"] = answers
        if joined_at is not UNSET:
            field_dict["joined_at"] = joined_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        person = PersonRef.from_dict(d.pop("person"))

        role = MemberRole(d.pop("role"))

        def _parse_answers(data: object) -> AgentAnswers | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answers_type_0 = AgentAnswers(data)

                return answers_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentAnswers | None | Unset, data)

        answers = _parse_answers(d.pop("answers", UNSET))

        def _parse_joined_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                joined_at_type_0 = isoparse(data)

                return joined_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        joined_at = _parse_joined_at(d.pop("joined_at", UNSET))

        channel_member_response = cls(
            person=person,
            role=role,
            answers=answers,
            joined_at=joined_at,
        )

        channel_member_response.additional_properties = d
        return channel_member_response

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
