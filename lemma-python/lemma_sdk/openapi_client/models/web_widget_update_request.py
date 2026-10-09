from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.widget_answer import WidgetAnswer
from ..types import UNSET, Unset

T = TypeVar("T", bound="WebWidgetUpdateRequest")


@_attrs_define
class WebWidgetUpdateRequest:
    """
    Attributes:
        allowed_origins (list[str] | None | Unset):
        answer (None | Unset | WidgetAnswer):
        looked_after_by (None | Unset | UUID):
    """

    allowed_origins: list[str] | None | Unset = UNSET
    answer: None | Unset | WidgetAnswer = UNSET
    looked_after_by: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed_origins: list[str] | None | Unset
        if isinstance(self.allowed_origins, Unset):
            allowed_origins = UNSET
        elif isinstance(self.allowed_origins, list):
            allowed_origins = self.allowed_origins

        else:
            allowed_origins = self.allowed_origins

        answer: None | str | Unset
        if isinstance(self.answer, Unset):
            answer = UNSET
        elif isinstance(self.answer, WidgetAnswer):
            answer = self.answer.value
        else:
            answer = self.answer

        looked_after_by: None | str | Unset
        if isinstance(self.looked_after_by, Unset):
            looked_after_by = UNSET
        elif isinstance(self.looked_after_by, UUID):
            looked_after_by = str(self.looked_after_by)
        else:
            looked_after_by = self.looked_after_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if allowed_origins is not UNSET:
            field_dict["allowed_origins"] = allowed_origins
        if answer is not UNSET:
            field_dict["answer"] = answer
        if looked_after_by is not UNSET:
            field_dict["looked_after_by"] = looked_after_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_allowed_origins(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                allowed_origins_type_0 = cast(list[str], data)

                return allowed_origins_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        allowed_origins = _parse_allowed_origins(d.pop("allowed_origins", UNSET))

        def _parse_answer(data: object) -> None | Unset | WidgetAnswer:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answer_type_0 = WidgetAnswer(data)

                return answer_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | WidgetAnswer, data)

        answer = _parse_answer(d.pop("answer", UNSET))

        def _parse_looked_after_by(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                looked_after_by_type_0 = UUID(data)

                return looked_after_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        looked_after_by = _parse_looked_after_by(d.pop("looked_after_by", UNSET))

        web_widget_update_request = cls(
            allowed_origins=allowed_origins,
            answer=answer,
            looked_after_by=looked_after_by,
        )

        web_widget_update_request.additional_properties = d
        return web_widget_update_request

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
