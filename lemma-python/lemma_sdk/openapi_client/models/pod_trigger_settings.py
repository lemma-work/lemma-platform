from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_trigger_settings_bulk_writes_default_type_0 import (
    PodTriggerSettingsBulkWritesDefaultType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodTriggerSettings")


@_attrs_define
class PodTriggerSettings:
    """Pod-wide defaults for the pod's DATASTORE triggers.

    Attributes:
        bulk_writes_default (None | PodTriggerSettingsBulkWritesDefaultType0 | Unset): What a bulk write does to this
            pod's triggers that do not say: skip (recorded as skipped), each (fire per matching row) or batch (one fire with
            the rows). Omitted means skip.
    """

    bulk_writes_default: None | PodTriggerSettingsBulkWritesDefaultType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bulk_writes_default: None | str | Unset
        if isinstance(self.bulk_writes_default, Unset):
            bulk_writes_default = UNSET
        elif isinstance(
            self.bulk_writes_default, PodTriggerSettingsBulkWritesDefaultType0
        ):
            bulk_writes_default = self.bulk_writes_default.value
        else:
            bulk_writes_default = self.bulk_writes_default

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if bulk_writes_default is not UNSET:
            field_dict["bulk_writes_default"] = bulk_writes_default

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_bulk_writes_default(
            data: object,
        ) -> None | PodTriggerSettingsBulkWritesDefaultType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                bulk_writes_default_type_0 = PodTriggerSettingsBulkWritesDefaultType0(
                    data
                )

                return bulk_writes_default_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodTriggerSettingsBulkWritesDefaultType0 | Unset, data)

        bulk_writes_default = _parse_bulk_writes_default(
            d.pop("bulk_writes_default", UNSET)
        )

        pod_trigger_settings = cls(
            bulk_writes_default=bulk_writes_default,
        )

        pod_trigger_settings.additional_properties = d
        return pod_trigger_settings

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
