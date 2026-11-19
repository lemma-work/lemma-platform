from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.reset_scope_result_scope import ResetScopeResultScope
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResetScopeResult")


@_attrs_define
class ResetScopeResult:
    """
    Attributes:
        scope (ResetScopeResultScope):
        detail (None | str | Unset):
        freed_bytes (int | None | Unset):
    """

    scope: ResetScopeResultScope
    detail: None | str | Unset = UNSET
    freed_bytes: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        scope = self.scope.value

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        freed_bytes: int | None | Unset
        if isinstance(self.freed_bytes, Unset):
            freed_bytes = UNSET
        else:
            freed_bytes = self.freed_bytes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "scope": scope,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail
        if freed_bytes is not UNSET:
            field_dict["freed_bytes"] = freed_bytes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        scope = ResetScopeResultScope(d.pop("scope"))

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        def _parse_freed_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        freed_bytes = _parse_freed_bytes(d.pop("freed_bytes", UNSET))

        reset_scope_result = cls(
            scope=scope,
            detail=detail,
            freed_bytes=freed_bytes,
        )

        reset_scope_result.additional_properties = d
        return reset_scope_result

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
