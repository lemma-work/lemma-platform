from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.row_result_response_answers import RowResultResponseAnswers


T = TypeVar("T", bound="RowResultResponse")


@_attrs_define
class RowResultResponse:
    """
    Attributes:
        answers (RowResultResponseAnswers):
        decision_id (None | UUID):
        failed (bool):
        index (int):
        open_ (list[str]):
        row_id (None | str):
    """

    answers: RowResultResponseAnswers
    decision_id: None | UUID
    failed: bool
    index: int
    open_: list[str]
    row_id: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answers = self.answers.to_dict()

        decision_id: None | str
        if isinstance(self.decision_id, UUID):
            decision_id = str(self.decision_id)
        else:
            decision_id = self.decision_id

        failed = self.failed

        index = self.index

        open_ = self.open_

        row_id: None | str
        row_id = self.row_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answers": answers,
                "decision_id": decision_id,
                "failed": failed,
                "index": index,
                "open": open_,
                "row_id": row_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.row_result_response_answers import RowResultResponseAnswers

        d = dict(src_dict)
        answers = RowResultResponseAnswers.from_dict(d.pop("answers"))

        def _parse_decision_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                decision_id_type_0 = UUID(data)

                return decision_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        decision_id = _parse_decision_id(d.pop("decision_id"))

        failed = d.pop("failed")

        index = d.pop("index")

        open_ = cast(list[str], d.pop("open"))

        def _parse_row_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        row_id = _parse_row_id(d.pop("row_id"))

        row_result_response = cls(
            answers=answers,
            decision_id=decision_id,
            failed=failed,
            index=index,
            open_=open_,
            row_id=row_id,
        )

        row_result_response.additional_properties = d
        return row_result_response

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
