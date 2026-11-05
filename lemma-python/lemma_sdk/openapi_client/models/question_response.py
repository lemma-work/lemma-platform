from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.question_kind import QuestionKind
from ..models.question_state import QuestionState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_ref import PersonRef
    from ..models.question_response_answer_type_0 import QuestionResponseAnswerType0
    from ..models.question_response_question import QuestionResponseQuestion


T = TypeVar("T", bound="QuestionResponse")


@_attrs_define
class QuestionResponse:
    """
    Attributes:
        awaits (PersonRef): Somebody in a channel, said one way everywhere: a member of the pod, one
            of its contacts, or one of its agents. There is nobody else -- everybody
            outside the pod is a contact.
        kind (QuestionKind):
        question (QuestionResponseQuestion): What is asked, in the asker's shape: a `prompt`, and `choices` or a form
            `schema` where it offers them.
        state (QuestionState):
        answer (None | QuestionResponseAnswerType0 | Unset):
        answered_at (datetime.datetime | None | Unset):
        answered_by (None | PersonRef | Unset):
        expires_at (datetime.datetime | None | Unset):
    """

    awaits: PersonRef
    kind: QuestionKind
    question: QuestionResponseQuestion
    state: QuestionState
    answer: None | QuestionResponseAnswerType0 | Unset = UNSET
    answered_at: datetime.datetime | None | Unset = UNSET
    answered_by: None | PersonRef | Unset = UNSET
    expires_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef
        from ..models.question_response_answer_type_0 import QuestionResponseAnswerType0

        awaits = self.awaits.to_dict()

        kind = self.kind.value

        question = self.question.to_dict()

        state = self.state.value

        answer: dict[str, Any] | None | Unset
        if isinstance(self.answer, Unset):
            answer = UNSET
        elif isinstance(self.answer, QuestionResponseAnswerType0):
            answer = self.answer.to_dict()
        else:
            answer = self.answer

        answered_at: None | str | Unset
        if isinstance(self.answered_at, Unset):
            answered_at = UNSET
        elif isinstance(self.answered_at, datetime.datetime):
            answered_at = self.answered_at.isoformat()
        else:
            answered_at = self.answered_at

        answered_by: dict[str, Any] | None | Unset
        if isinstance(self.answered_by, Unset):
            answered_by = UNSET
        elif isinstance(self.answered_by, PersonRef):
            answered_by = self.answered_by.to_dict()
        else:
            answered_by = self.answered_by

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        elif isinstance(self.expires_at, datetime.datetime):
            expires_at = self.expires_at.isoformat()
        else:
            expires_at = self.expires_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "awaits": awaits,
                "kind": kind,
                "question": question,
                "state": state,
            }
        )
        if answer is not UNSET:
            field_dict["answer"] = answer
        if answered_at is not UNSET:
            field_dict["answered_at"] = answered_at
        if answered_by is not UNSET:
            field_dict["answered_by"] = answered_by
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef
        from ..models.question_response_answer_type_0 import QuestionResponseAnswerType0
        from ..models.question_response_question import QuestionResponseQuestion

        d = dict(src_dict)
        awaits = PersonRef.from_dict(d.pop("awaits"))

        kind = QuestionKind(d.pop("kind"))

        question = QuestionResponseQuestion.from_dict(d.pop("question"))

        state = QuestionState(d.pop("state"))

        def _parse_answer(data: object) -> None | QuestionResponseAnswerType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                answer_type_0 = QuestionResponseAnswerType0.from_dict(data)

                return answer_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | QuestionResponseAnswerType0 | Unset, data)

        answer = _parse_answer(d.pop("answer", UNSET))

        def _parse_answered_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answered_at_type_0 = isoparse(data)

                return answered_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        answered_at = _parse_answered_at(d.pop("answered_at", UNSET))

        def _parse_answered_by(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                answered_by_type_0 = PersonRef.from_dict(data)

                return answered_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        answered_by = _parse_answered_by(d.pop("answered_by", UNSET))

        def _parse_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_at_type_0 = isoparse(data)

                return expires_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        question_response = cls(
            awaits=awaits,
            kind=kind,
            question=question,
            state=state,
            answer=answer,
            answered_at=answered_at,
            answered_by=answered_by,
            expires_at=expires_at,
        )

        question_response.additional_properties = d
        return question_response

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
