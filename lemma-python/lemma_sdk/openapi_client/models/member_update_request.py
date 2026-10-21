from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_answers import AgentAnswers
from ..models.member_role import MemberRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="MemberUpdateRequest")


@_attrs_define
class MemberUpdateRequest:
    """
    Attributes:
        answers (AgentAnswers | None | Unset): An agent's.
        role (MemberRole | None | Unset): A person's.
    """

    answers: AgentAnswers | None | Unset = UNSET
    role: MemberRole | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answers: None | str | Unset
        if isinstance(self.answers, Unset):
            answers = UNSET
        elif isinstance(self.answers, AgentAnswers):
            answers = self.answers.value
        else:
            answers = self.answers

        role: None | str | Unset
        if isinstance(self.role, Unset):
            role = UNSET
        elif isinstance(self.role, MemberRole):
            role = self.role.value
        else:
            role = self.role

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if answers is not UNSET:
            field_dict["answers"] = answers
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

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

        def _parse_role(data: object) -> MemberRole | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                role_type_0 = MemberRole(data)

                return role_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(MemberRole | None | Unset, data)

        role = _parse_role(d.pop("role", UNSET))

        member_update_request = cls(
            answers=answers,
            role=role,
        )

        member_update_request.additional_properties = d
        return member_update_request

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
