from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_answers import AgentAnswers
from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentSeat")


@_attrs_define
class AgentSeat:
    """
    Attributes:
        id (UUID): One of the pod's agents.
        answers (AgentAnswers | Unset): When an agent in a channel answers.
    """

    id: UUID
    answers: AgentAnswers | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        answers: str | Unset = UNSET
        if not isinstance(self.answers, Unset):
            answers = self.answers.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if answers is not UNSET:
            field_dict["answers"] = answers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        _answers = d.pop("answers", UNSET)
        answers: AgentAnswers | Unset
        if isinstance(_answers, Unset):
            answers = UNSET
        else:
            answers = AgentAnswers(_answers)

        agent_seat = cls(
            id=id,
            answers=answers,
        )

        agent_seat.additional_properties = d
        return agent_seat

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
