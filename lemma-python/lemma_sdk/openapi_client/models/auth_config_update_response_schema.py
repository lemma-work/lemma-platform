from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.auth_config_response_schema import AuthConfigResponseSchema
    from ..models.operation_discovery_schema import OperationDiscoverySchema


T = TypeVar("T", bound="AuthConfigUpdateResponseSchema")


@_attrs_define
class AuthConfigUpdateResponseSchema:
    """
    Attributes:
        auth_config (AuthConfigResponseSchema):
        operations_discovery (OperationDiscoverySchema): What re-reading an install's operation list actually did.

            `operation_count` alone cannot say: a connector with no operations to
            advertise, a kind whose operations are static, and a server that refused
            the listing all report zero. They need different things from the reader --
            nothing, nothing, and a retry once the server is reachable -- so the status
            is the field to branch on and the count is detail.
        accounts_marked_for_reauth (int | Unset): Connected accounts flagged for reconnect because the change
            invalidated their stored credentials. They are never deleted: the account keeps its id and grants, and
            reconnecting updates it in place, so anything referencing it keeps working. Default: 0.
        operations_discovered (int | Unset): Operations re-discovered because the change altered where they come from.
            Zero for a connector whose operations are static, and also zero when discovery was refused -- read
            `operations_discovery.status` to tell those apart. Default: 0.
    """

    auth_config: AuthConfigResponseSchema
    operations_discovery: OperationDiscoverySchema
    accounts_marked_for_reauth: int | Unset = 0
    operations_discovered: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_config = self.auth_config.to_dict()

        operations_discovery = self.operations_discovery.to_dict()

        accounts_marked_for_reauth = self.accounts_marked_for_reauth

        operations_discovered = self.operations_discovered

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_config": auth_config,
                "operations_discovery": operations_discovery,
            }
        )
        if accounts_marked_for_reauth is not UNSET:
            field_dict["accounts_marked_for_reauth"] = accounts_marked_for_reauth
        if operations_discovered is not UNSET:
            field_dict["operations_discovered"] = operations_discovered

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.auth_config_response_schema import AuthConfigResponseSchema
        from ..models.operation_discovery_schema import OperationDiscoverySchema

        d = dict(src_dict)
        auth_config = AuthConfigResponseSchema.from_dict(d.pop("auth_config"))

        operations_discovery = OperationDiscoverySchema.from_dict(
            d.pop("operations_discovery")
        )

        accounts_marked_for_reauth = d.pop("accounts_marked_for_reauth", UNSET)

        operations_discovered = d.pop("operations_discovered", UNSET)

        auth_config_update_response_schema = cls(
            auth_config=auth_config,
            operations_discovery=operations_discovery,
            accounts_marked_for_reauth=accounts_marked_for_reauth,
            operations_discovered=operations_discovered,
        )

        auth_config_update_response_schema.additional_properties = d
        return auth_config_update_response_schema

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
