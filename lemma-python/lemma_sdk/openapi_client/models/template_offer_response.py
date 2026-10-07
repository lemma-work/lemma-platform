from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="TemplateOfferResponse")


@_attrs_define
class TemplateOfferResponse:
    """
    Attributes:
        cron (str):
        detail (str):
        instruction (str):
        title (str):
    """

    cron: str
    detail: str
    instruction: str
    title: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        detail = self.detail

        instruction = self.instruction

        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cron": cron,
                "detail": detail,
                "instruction": instruction,
                "title": title,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cron = d.pop("cron")

        detail = d.pop("detail")

        instruction = d.pop("instruction")

        title = d.pop("title")

        template_offer_response = cls(
            cron=cron,
            detail=detail,
            instruction=instruction,
            title=title,
        )

        template_offer_response.additional_properties = d
        return template_offer_response

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
