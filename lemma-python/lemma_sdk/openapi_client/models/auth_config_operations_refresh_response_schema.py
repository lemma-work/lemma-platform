from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.operation_discovery_status import OperationDiscoveryStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="AuthConfigOperationsRefreshResponseSchema")


@_attrs_define
class AuthConfigOperationsRefreshResponseSchema:
    """The result of the refresh endpoint, which is the recovery path.

    It reports the outcome rather than only a count because it exists for the
    case where discovery already failed once: answering `{"operation_count": 0}`
    to a server that refused the listing again told the operator their retry
    had worked.

        Attributes:
            auth_config_name (str):
            status (OperationDiscoveryStatus): Whether a discovery attempt worked, was never made, or was refused.
            operation_count (int | Unset): Operations stored for the install. Zero unless status is ok. Default: 0.
            reason (None | str | Unset): Machine-readable cause when status is not ok: the connector error code for a
                refused discovery, or why none was attempted.
    """

    auth_config_name: str
    status: OperationDiscoveryStatus
    operation_count: int | Unset = 0
    reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_config_name = self.auth_config_name

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
                "auth_config_name": auth_config_name,
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
        auth_config_name = d.pop("auth_config_name")

        status = OperationDiscoveryStatus(d.pop("status"))

        operation_count = d.pop("operation_count", UNSET)

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        auth_config_operations_refresh_response_schema = cls(
            auth_config_name=auth_config_name,
            status=status,
            operation_count=operation_count,
            reason=reason,
        )

        auth_config_operations_refresh_response_schema.additional_properties = d
        return auth_config_operations_refresh_response_schema

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
