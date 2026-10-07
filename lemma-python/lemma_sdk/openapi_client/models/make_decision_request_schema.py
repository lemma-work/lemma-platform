from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MakeDecisionRequestSchema")


@_attrs_define
class MakeDecisionRequestSchema:
    """The questions, as a flat JSON Schema object: one property per question, its `description` the question. Each
    property is a choice (`{"type": "string", "enum": [...]}`, or `oneOf` of `{const, description}`), a multi-choice
    (`{"type": "array", "items": <choice>, "uniqueItems": true}`), yes or no (`{"type": "boolean"}`), or a scale
    (`{"type": "integer", "minimum": 1, "maximum": 5}`, or `oneOf` of described integer levels). Free text and open-
    ended numbers are not supported.

    """

    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        make_decision_request_schema = cls()

        make_decision_request_schema.additional_properties = d
        return make_decision_request_schema

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
