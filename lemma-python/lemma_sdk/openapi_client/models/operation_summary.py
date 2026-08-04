from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OperationSummary")


@_attrs_define
class OperationSummary:
    """Compact operation metadata for discovery flows.

    Attributes:
        name (str):
        auth_config (None | str | Unset): Install this operation belongs to (org-wide search only).
        connector_id (None | str | Unset): Connector this operation belongs to (org-wide search only).
        description (None | str | Unset):
        relevance_score (float | None | Unset): Relative relevance for the discovery query, from 0 to 1.
    """

    name: str
    auth_config: None | str | Unset = UNSET
    connector_id: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    relevance_score: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        auth_config: None | str | Unset
        if isinstance(self.auth_config, Unset):
            auth_config = UNSET
        else:
            auth_config = self.auth_config

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        relevance_score: float | None | Unset
        if isinstance(self.relevance_score, Unset):
            relevance_score = UNSET
        else:
            relevance_score = self.relevance_score

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if auth_config is not UNSET:
            field_dict["auth_config"] = auth_config
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id
        if description is not UNSET:
            field_dict["description"] = description
        if relevance_score is not UNSET:
            field_dict["relevance_score"] = relevance_score

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_auth_config(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_config = _parse_auth_config(d.pop("auth_config", UNSET))

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_relevance_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        relevance_score = _parse_relevance_score(d.pop("relevance_score", UNSET))

        operation_summary = cls(
            name=name,
            auth_config=auth_config,
            connector_id=connector_id,
            description=description,
            relevance_score=relevance_score,
        )

        operation_summary.additional_properties = d
        return operation_summary

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
