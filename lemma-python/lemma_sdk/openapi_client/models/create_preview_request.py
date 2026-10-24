from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.create_preview_request_visibility import CreatePreviewRequestVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="CreatePreviewRequest")


@_attrs_define
class CreatePreviewRequest:
    """
    Attributes:
        port (int):
        ttl_seconds (int | None | Unset): How long it lives. Required-ish for `link`: capped at 24 hours.
        visibility (CreatePreviewRequestVisibility | Unset): `private`: people who can see the machine, after signing
            in. `link`: anyone with the URL, for at most 24 hours. Default: CreatePreviewRequestVisibility.PRIVATE.
    """

    port: int
    ttl_seconds: int | None | Unset = UNSET
    visibility: CreatePreviewRequestVisibility | Unset = (
        CreatePreviewRequestVisibility.PRIVATE
    )
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        port = self.port

        ttl_seconds: int | None | Unset
        if isinstance(self.ttl_seconds, Unset):
            ttl_seconds = UNSET
        else:
            ttl_seconds = self.ttl_seconds

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "port": port,
            }
        )
        if ttl_seconds is not UNSET:
            field_dict["ttl_seconds"] = ttl_seconds
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        port = d.pop("port")

        def _parse_ttl_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        ttl_seconds = _parse_ttl_seconds(d.pop("ttl_seconds", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: CreatePreviewRequestVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = CreatePreviewRequestVisibility(_visibility)

        create_preview_request = cls(
            port=port,
            ttl_seconds=ttl_seconds,
            visibility=visibility,
        )

        create_preview_request.additional_properties = d
        return create_preview_request

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
