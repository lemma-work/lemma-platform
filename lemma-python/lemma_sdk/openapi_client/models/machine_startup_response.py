from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.machine_startup_response_state import MachineStartupResponseState
from ..types import UNSET, Unset

T = TypeVar("T", bound="MachineStartupResponse")


@_attrs_define
class MachineStartupResponse:
    """Whether the machine is ready and, if not, what it is doing. Never wakes it.

    Attributes:
        state (MachineStartupResponseState): `ready`: running. `downloading`: fetching its image, which the first start
            after an update does. `starting`: coming up. `asleep`: not running, and starts on first use. `unavailable`:
            could not be asked.
        detail (None | str | Unset):
        disk_total_bytes (int | None | Unset):
        disk_used_bytes (int | None | Unset):
        done_mb (int | None | Unset):
        total_mb (int | None | Unset):
    """

    state: MachineStartupResponseState
    detail: None | str | Unset = UNSET
    disk_total_bytes: int | None | Unset = UNSET
    disk_used_bytes: int | None | Unset = UNSET
    done_mb: int | None | Unset = UNSET
    total_mb: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        disk_total_bytes: int | None | Unset
        if isinstance(self.disk_total_bytes, Unset):
            disk_total_bytes = UNSET
        else:
            disk_total_bytes = self.disk_total_bytes

        disk_used_bytes: int | None | Unset
        if isinstance(self.disk_used_bytes, Unset):
            disk_used_bytes = UNSET
        else:
            disk_used_bytes = self.disk_used_bytes

        done_mb: int | None | Unset
        if isinstance(self.done_mb, Unset):
            done_mb = UNSET
        else:
            done_mb = self.done_mb

        total_mb: int | None | Unset
        if isinstance(self.total_mb, Unset):
            total_mb = UNSET
        else:
            total_mb = self.total_mb

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail
        if disk_total_bytes is not UNSET:
            field_dict["disk_total_bytes"] = disk_total_bytes
        if disk_used_bytes is not UNSET:
            field_dict["disk_used_bytes"] = disk_used_bytes
        if done_mb is not UNSET:
            field_dict["done_mb"] = done_mb
        if total_mb is not UNSET:
            field_dict["total_mb"] = total_mb

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        state = MachineStartupResponseState(d.pop("state"))

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        def _parse_disk_total_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        disk_total_bytes = _parse_disk_total_bytes(d.pop("disk_total_bytes", UNSET))

        def _parse_disk_used_bytes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        disk_used_bytes = _parse_disk_used_bytes(d.pop("disk_used_bytes", UNSET))

        def _parse_done_mb(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        done_mb = _parse_done_mb(d.pop("done_mb", UNSET))

        def _parse_total_mb(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        total_mb = _parse_total_mb(d.pop("total_mb", UNSET))

        machine_startup_response = cls(
            state=state,
            detail=detail,
            disk_total_bytes=disk_total_bytes,
            disk_used_bytes=disk_used_bytes,
            done_mb=done_mb,
            total_mb=total_mb,
        )

        machine_startup_response.additional_properties = d
        return machine_startup_response

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
