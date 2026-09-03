from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.search_method import SearchMethod
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_search_result_schema import FileSearchResultSchema


T = TypeVar("T", bound="FileSearchResponse")


@_attrs_define
class FileSearchResponse:
    """
    Attributes:
        items (list[FileSearchResultSchema]):
        query (str):
        search_method (SearchMethod):
        total (int): Number of matches in `items`. This is what came back, not how many matches the pod holds: when
            `truncated` is true the result was cut at `limit` and more exist.
        truncated (bool | Unset): True when the result filled the requested `limit`, so there are likely further matches
            this response does not show. Narrow the query or raise `limit` to see more. Reported because a capped result is
            otherwise indistinguishable from a complete one — an agent reading `total` as the number of matching documents
            states it to a person as fact. Default: False.
    """

    items: list[FileSearchResultSchema]
    query: str
    search_method: SearchMethod
    total: int
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        query = self.query

        search_method = self.search_method.value

        total = self.total

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "query": query,
                "search_method": search_method,
                "total": total,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_search_result_schema import FileSearchResultSchema

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = FileSearchResultSchema.from_dict(items_item_data)

            items.append(items_item)

        query = d.pop("query")

        search_method = SearchMethod(d.pop("search_method"))

        total = d.pop("total")

        truncated = d.pop("truncated", UNSET)

        file_search_response = cls(
            items=items,
            query=query,
            search_method=search_method,
            total=total,
            truncated=truncated,
        )

        file_search_response.additional_properties = d
        return file_search_response

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
