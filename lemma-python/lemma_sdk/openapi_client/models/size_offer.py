from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.size_offer_template import SizeOfferTemplate

T = TypeVar("T", bound="SizeOffer")


@_attrs_define
class SizeOffer:
    """
    Attributes:
        sizes (list[str]):
        template (SizeOfferTemplate):
    """

    sizes: list[str]
    template: SizeOfferTemplate
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        sizes = self.sizes

        template = self.template.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "sizes": sizes,
                "template": template,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        sizes = cast(list[str], d.pop("sizes"))

        template = SizeOfferTemplate(d.pop("template"))

        size_offer = cls(
            sizes=sizes,
            template=template,
        )

        size_offer.additional_properties = d
        return size_offer

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
