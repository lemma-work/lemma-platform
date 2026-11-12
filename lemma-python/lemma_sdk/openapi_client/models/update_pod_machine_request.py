from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.update_pod_machine_request_visibility_type_0 import (
    UpdatePodMachineRequestVisibilityType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdatePodMachineRequest")


@_attrs_define
class UpdatePodMachineRequest:
    """
    Attributes:
        display_name (None | str | Unset):
        run_slots (int | None | Unset):
        size (None | str | Unset):
        visibility (None | Unset | UpdatePodMachineRequestVisibilityType0):
    """

    display_name: None | str | Unset = UNSET
    run_slots: int | None | Unset = UNSET
    size: None | str | Unset = UNSET
    visibility: None | Unset | UpdatePodMachineRequestVisibilityType0 = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        run_slots: int | None | Unset
        if isinstance(self.run_slots, Unset):
            run_slots = UNSET
        else:
            run_slots = self.run_slots

        size: None | str | Unset
        if isinstance(self.size, Unset):
            size = UNSET
        else:
            size = self.size

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, UpdatePodMachineRequestVisibilityType0):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if run_slots is not UNSET:
            field_dict["run_slots"] = run_slots
        if size is not UNSET:
            field_dict["size"] = size
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        def _parse_run_slots(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        run_slots = _parse_run_slots(d.pop("run_slots", UNSET))

        def _parse_size(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        size = _parse_size(d.pop("size", UNSET))

        def _parse_visibility(
            data: object,
        ) -> None | Unset | UpdatePodMachineRequestVisibilityType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = UpdatePodMachineRequestVisibilityType0(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdatePodMachineRequestVisibilityType0, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update_pod_machine_request = cls(
            display_name=display_name,
            run_slots=run_slots,
            size=size,
            visibility=visibility,
        )

        update_pod_machine_request.additional_properties = d
        return update_pod_machine_request

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
