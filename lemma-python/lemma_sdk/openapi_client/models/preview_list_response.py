from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.preview_response import PreviewResponse


T = TypeVar("T", bound="PreviewListResponse")


@_attrs_define
class PreviewListResponse:
    """
    Attributes:
        previews (list[PreviewResponse]):
    """

    previews: list[PreviewResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        previews = []
        for previews_item_data in self.previews:
            previews_item = previews_item_data.to_dict()
            previews.append(previews_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "previews": previews,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.preview_response import PreviewResponse

        d = dict(src_dict)
        previews = []
        _previews = d.pop("previews")
        for previews_item_data in _previews:
            previews_item = PreviewResponse.from_dict(previews_item_data)

            previews.append(previews_item)

        preview_list_response = cls(
            previews=previews,
        )

        preview_list_response.additional_properties = d
        return preview_list_response

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
