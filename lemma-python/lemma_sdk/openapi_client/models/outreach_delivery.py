from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.delivery_status import DeliveryStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="OutreachDelivery")


@_attrs_define
class OutreachDelivery:
    """
    Attributes:
        id (UUID):
        status (DeliveryStatus):
        platform (None | str | Unset):
        reason (None | str | Unset):
        source_id (None | Unset | UUID):
    """

    id: UUID
    status: DeliveryStatus
    platform: None | str | Unset = UNSET
    reason: None | str | Unset = UNSET
    source_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        status = self.status.value

        platform: None | str | Unset
        if isinstance(self.platform, Unset):
            platform = UNSET
        else:
            platform = self.platform

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "status": status,
            }
        )
        if platform is not UNSET:
            field_dict["platform"] = platform
        if reason is not UNSET:
            field_dict["reason"] = reason
        if source_id is not UNSET:
            field_dict["source_id"] = source_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        status = DeliveryStatus(d.pop("status"))

        def _parse_platform(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        platform = _parse_platform(d.pop("platform", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_source_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_id_type_0 = UUID(data)

                return source_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        outreach_delivery = cls(
            id=id,
            status=status,
            platform=platform,
            reason=reason,
            source_id=source_id,
        )

        outreach_delivery.additional_properties = d
        return outreach_delivery

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
