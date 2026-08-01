from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceSystemClaim")


@_attrs_define
class SurfaceSystemClaim:
    """Whether this org can still put the platform's Lemma-managed bot/number
    behind a surface.

    The shared identity is claimable exactly once per organization, so the setup
    UI can render the option as unavailable *before* the user commits instead of
    discovering it as a failed save. ``claimed_by_pod_id`` is the pod holding the
    claim — always a pod in the caller's own org, so linking to it leaks nothing
    they can't already see.

        Attributes:
            available (bool):
            claimed_by_pod_id (None | Unset | UUID):
            claimed_by_surface_name (None | str | Unset):
    """

    available: bool
    claimed_by_pod_id: None | Unset | UUID = UNSET
    claimed_by_surface_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        available = self.available

        claimed_by_pod_id: None | str | Unset
        if isinstance(self.claimed_by_pod_id, Unset):
            claimed_by_pod_id = UNSET
        elif isinstance(self.claimed_by_pod_id, UUID):
            claimed_by_pod_id = str(self.claimed_by_pod_id)
        else:
            claimed_by_pod_id = self.claimed_by_pod_id

        claimed_by_surface_name: None | str | Unset
        if isinstance(self.claimed_by_surface_name, Unset):
            claimed_by_surface_name = UNSET
        else:
            claimed_by_surface_name = self.claimed_by_surface_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "available": available,
            }
        )
        if claimed_by_pod_id is not UNSET:
            field_dict["claimed_by_pod_id"] = claimed_by_pod_id
        if claimed_by_surface_name is not UNSET:
            field_dict["claimed_by_surface_name"] = claimed_by_surface_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        available = d.pop("available")

        def _parse_claimed_by_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                claimed_by_pod_id_type_0 = UUID(data)

                return claimed_by_pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        claimed_by_pod_id = _parse_claimed_by_pod_id(d.pop("claimed_by_pod_id", UNSET))

        def _parse_claimed_by_surface_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        claimed_by_surface_name = _parse_claimed_by_surface_name(
            d.pop("claimed_by_surface_name", UNSET)
        )

        surface_system_claim = cls(
            available=available,
            claimed_by_pod_id=claimed_by_pod_id,
            claimed_by_surface_name=claimed_by_surface_name,
        )

        surface_system_claim.additional_properties = d
        return surface_system_claim

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
