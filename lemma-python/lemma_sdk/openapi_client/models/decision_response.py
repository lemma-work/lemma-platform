from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.decision_response_answers import DecisionResponseAnswers
    from ..models.rung_trace import RungTrace


T = TypeVar("T", bound="DecisionResponse")


@_attrs_define
class DecisionResponse:
    """
    Attributes:
        answered_at (datetime.datetime | None):
        answered_by_user_id (None | UUID):
        answers (DecisionResponseAnswers):
        created_at (datetime.datetime):
        decider_name (None | str):
        decider_scope (str):
        decider_version (int | None):
        evidence (None | str):
        id (UUID):
        open_ (list[str]):
        status (str):
        subject_key (None | str):
        trace (list[RungTrace]):
        visibility (str):
    """

    answered_at: datetime.datetime | None
    answered_by_user_id: None | UUID
    answers: DecisionResponseAnswers
    created_at: datetime.datetime
    decider_name: None | str
    decider_scope: str
    decider_version: int | None
    evidence: None | str
    id: UUID
    open_: list[str]
    status: str
    subject_key: None | str
    trace: list[RungTrace]
    visibility: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answered_at: None | str
        if isinstance(self.answered_at, datetime.datetime):
            answered_at = self.answered_at.isoformat()
        else:
            answered_at = self.answered_at

        answered_by_user_id: None | str
        if isinstance(self.answered_by_user_id, UUID):
            answered_by_user_id = str(self.answered_by_user_id)
        else:
            answered_by_user_id = self.answered_by_user_id

        answers = self.answers.to_dict()

        created_at = self.created_at.isoformat()

        decider_name: None | str
        decider_name = self.decider_name

        decider_scope = self.decider_scope

        decider_version: int | None
        decider_version = self.decider_version

        evidence: None | str
        evidence = self.evidence

        id = str(self.id)

        open_ = self.open_

        status = self.status

        subject_key: None | str
        subject_key = self.subject_key

        trace = []
        for trace_item_data in self.trace:
            trace_item = trace_item_data.to_dict()
            trace.append(trace_item)

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answered_at": answered_at,
                "answered_by_user_id": answered_by_user_id,
                "answers": answers,
                "created_at": created_at,
                "decider_name": decider_name,
                "decider_scope": decider_scope,
                "decider_version": decider_version,
                "evidence": evidence,
                "id": id,
                "open": open_,
                "status": status,
                "subject_key": subject_key,
                "trace": trace,
                "visibility": visibility,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_response_answers import DecisionResponseAnswers
        from ..models.rung_trace import RungTrace

        d = dict(src_dict)

        def _parse_answered_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answered_at_type_0 = isoparse(data)

                return answered_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        answered_at = _parse_answered_at(d.pop("answered_at"))

        def _parse_answered_by_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answered_by_user_id_type_0 = UUID(data)

                return answered_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        answered_by_user_id = _parse_answered_by_user_id(d.pop("answered_by_user_id"))

        answers = DecisionResponseAnswers.from_dict(d.pop("answers"))

        created_at = isoparse(d.pop("created_at"))

        def _parse_decider_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        decider_name = _parse_decider_name(d.pop("decider_name"))

        decider_scope = d.pop("decider_scope")

        def _parse_decider_version(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        decider_version = _parse_decider_version(d.pop("decider_version"))

        def _parse_evidence(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        evidence = _parse_evidence(d.pop("evidence"))

        id = UUID(d.pop("id"))

        open_ = cast(list[str], d.pop("open"))

        status = d.pop("status")

        def _parse_subject_key(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        subject_key = _parse_subject_key(d.pop("subject_key"))

        trace = []
        _trace = d.pop("trace")
        for trace_item_data in _trace:
            trace_item = RungTrace.from_dict(trace_item_data)

            trace.append(trace_item)

        visibility = d.pop("visibility")

        decision_response = cls(
            answered_at=answered_at,
            answered_by_user_id=answered_by_user_id,
            answers=answers,
            created_at=created_at,
            decider_name=decider_name,
            decider_scope=decider_scope,
            decider_version=decider_version,
            evidence=evidence,
            id=id,
            open_=open_,
            status=status,
            subject_key=subject_key,
            trace=trace,
            visibility=visibility,
        )

        decision_response.additional_properties = d
        return decision_response

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
