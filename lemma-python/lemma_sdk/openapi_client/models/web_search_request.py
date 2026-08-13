from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.search_freshness import SearchFreshness
from ..models.search_vertical import SearchVertical
from ..types import UNSET, Unset

T = TypeVar("T", bound="WebSearchRequest")


@_attrs_define
class WebSearchRequest:
    """Request model for standard web search

    Attributes:
        query (str): Search query. Use specific keywords rather than a question, and prefer
            `include_domains`/`exclude_domains` over typing `site:` yourself.
        exclude_domains (list[str] | None | Unset): Drop results from these domains, e.g. ['pinterest.com'].
        freshness (None | SearchFreshness | Unset): Only results from the past `day`, `week`, `month`, or `year`. Use it
            for anything time-sensitive — search engines happily return five-year-old pages for current questions.
        include_domains (list[str] | None | Unset): Restrict results to these domains, e.g. ['arxiv.org'].
        max_results (int | Unset): Maximum number of search results to return Default: 10.
        vertical (SearchVertical | Unset): What kind of result the caller wants.

            Not every provider serves every vertical, so `BaseSearchClient` advertises
            what it supports and the caller degrades honestly rather than silently
            returning web pages for a video query.
    """

    query: str
    exclude_domains: list[str] | None | Unset = UNSET
    freshness: None | SearchFreshness | Unset = UNSET
    include_domains: list[str] | None | Unset = UNSET
    max_results: int | Unset = 10
    vertical: SearchVertical | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        exclude_domains: list[str] | None | Unset
        if isinstance(self.exclude_domains, Unset):
            exclude_domains = UNSET
        elif isinstance(self.exclude_domains, list):
            exclude_domains = self.exclude_domains

        else:
            exclude_domains = self.exclude_domains

        freshness: None | str | Unset
        if isinstance(self.freshness, Unset):
            freshness = UNSET
        elif isinstance(self.freshness, SearchFreshness):
            freshness = self.freshness.value
        else:
            freshness = self.freshness

        include_domains: list[str] | None | Unset
        if isinstance(self.include_domains, Unset):
            include_domains = UNSET
        elif isinstance(self.include_domains, list):
            include_domains = self.include_domains

        else:
            include_domains = self.include_domains

        max_results = self.max_results

        vertical: str | Unset = UNSET
        if not isinstance(self.vertical, Unset):
            vertical = self.vertical.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "query": query,
            }
        )
        if exclude_domains is not UNSET:
            field_dict["exclude_domains"] = exclude_domains
        if freshness is not UNSET:
            field_dict["freshness"] = freshness
        if include_domains is not UNSET:
            field_dict["include_domains"] = include_domains
        if max_results is not UNSET:
            field_dict["max_results"] = max_results
        if vertical is not UNSET:
            field_dict["vertical"] = vertical

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        query = d.pop("query")

        def _parse_exclude_domains(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                exclude_domains_type_0 = cast(list[str], data)

                return exclude_domains_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        exclude_domains = _parse_exclude_domains(d.pop("exclude_domains", UNSET))

        def _parse_freshness(data: object) -> None | SearchFreshness | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                freshness_type_0 = SearchFreshness(data)

                return freshness_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SearchFreshness | Unset, data)

        freshness = _parse_freshness(d.pop("freshness", UNSET))

        def _parse_include_domains(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                include_domains_type_0 = cast(list[str], data)

                return include_domains_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        include_domains = _parse_include_domains(d.pop("include_domains", UNSET))

        max_results = d.pop("max_results", UNSET)

        _vertical = d.pop("vertical", UNSET)
        vertical: SearchVertical | Unset
        if isinstance(_vertical, Unset):
            vertical = UNSET
        else:
            vertical = SearchVertical(_vertical)

        web_search_request = cls(
            query=query,
            exclude_domains=exclude_domains,
            freshness=freshness,
            include_domains=include_domains,
            max_results=max_results,
            vertical=vertical,
        )

        web_search_request.additional_properties = d
        return web_search_request

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
