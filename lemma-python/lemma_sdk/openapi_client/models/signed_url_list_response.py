from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.signed_url_summary import SignedUrlSummary


T = TypeVar("T", bound="SignedUrlListResponse")


@_attrs_define
class SignedUrlListResponse:
    """
    Attributes:
        links (list[SignedUrlSummary]):
    """

    links: list[SignedUrlSummary]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        links = []
        for links_item_data in self.links:
            links_item = links_item_data.to_dict()
            links.append(links_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "links": links,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.signed_url_summary import SignedUrlSummary

        d = dict(src_dict)
        links = []
        _links = d.pop("links")
        for links_item_data in _links:
            links_item = SignedUrlSummary.from_dict(links_item_data)

            links.append(links_item)

        signed_url_list_response = cls(
            links=links,
        )

        signed_url_list_response.additional_properties = d
        return signed_url_list_response

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
