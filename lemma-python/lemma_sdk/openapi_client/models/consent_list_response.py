from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.consent_response import ConsentResponse


T = TypeVar("T", bound="ConsentListResponse")


@_attrs_define
class ConsentListResponse:
    """
    Attributes:
        consents (list[ConsentResponse]):
    """

    consents: list[ConsentResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        consents = []
        for consents_item_data in self.consents:
            consents_item = consents_item_data.to_dict()
            consents.append(consents_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "consents": consents,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.consent_response import ConsentResponse

        d = dict(src_dict)
        consents = []
        _consents = d.pop("consents")
        for consents_item_data in _consents:
            consents_item = ConsentResponse.from_dict(consents_item_data)

            consents.append(consents_item)

        consent_list_response = cls(
            consents=consents,
        )

        consent_list_response.additional_properties = d
        return consent_list_response

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
