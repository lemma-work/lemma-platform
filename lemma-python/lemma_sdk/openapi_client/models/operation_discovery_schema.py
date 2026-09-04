from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.operation_discovery_status import OperationDiscoveryStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="OperationDiscoverySchema")


@_attrs_define
class OperationDiscoverySchema:
    """What re-reading an install's operation list actually did.

    `operation_count` alone cannot say: a connector with no operations to
    advertise, a kind whose operations are static, and a server that refused
    the listing all report zero. They need different things from the reader --
    nothing, nothing, and a retry once the server is reachable -- so the status
    is the field to branch on and the count is detail.

        Attributes:
            status (OperationDiscoveryStatus): Whether a discovery attempt worked, was never made, or was refused.
            operation_count (int | Unset): Operations stored for the install. Zero unless status is ok. Default: 0.
            reason (None | str | Unset): Machine-readable cause when status is not ok: the connector error code for a
                refused discovery, or why none was attempted.
    """

    status: OperationDiscoveryStatus
    operation_count: int | Unset = 0
    reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        operation_count = self.operation_count

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if operation_count is not UNSET:
            field_dict["operation_count"] = operation_count
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        status = OperationDiscoveryStatus(d.pop("status"))

        operation_count = d.pop("operation_count", UNSET)

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        operation_discovery_schema = cls(
            status=status,
            operation_count=operation_count,
            reason=reason,
        )

        operation_discovery_schema.additional_properties = d
        return operation_discovery_schema

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
