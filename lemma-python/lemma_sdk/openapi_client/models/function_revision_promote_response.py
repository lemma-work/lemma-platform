from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.function_revision_response import FunctionRevisionResponse


T = TypeVar("T", bound="FunctionRevisionPromoteResponse")


@_attrs_define
class FunctionRevisionPromoteResponse:
    """
    Attributes:
        revision (FunctionRevisionResponse): One entry in a function's revision history.
        schema_changed (bool): True when this revision's input/output/config schemas differ from the ones that were
            live. The schemas move with the revision, so agents and workflows bound to the old contract may need updating.
    """

    revision: FunctionRevisionResponse
    schema_changed: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        revision = self.revision.to_dict()

        schema_changed = self.schema_changed

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "revision": revision,
                "schema_changed": schema_changed,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.function_revision_response import FunctionRevisionResponse

        d = dict(src_dict)
        revision = FunctionRevisionResponse.from_dict(d.pop("revision"))

        schema_changed = d.pop("schema_changed")

        function_revision_promote_response = cls(
            revision=revision,
            schema_changed=schema_changed,
        )

        function_revision_promote_response.additional_properties = d
        return function_revision_promote_response

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
