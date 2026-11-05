from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_connection_status import SurfaceConnectionStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_connection_owner import SurfaceConnectionOwner


T = TypeVar("T", bound="SurfaceConnection")


@_attrs_define
class SurfaceConnection:
    """Which account a surface's pod connector runs as, and whose it is.

    Accounts are personal (``accounts.user_id``) while surfaces belong to the
    pod, so an account id alone answers nothing for a teammate — they cannot
    resolve an id they don't own. This block is the pod-visible *identity* of
    that account: enough for any editor to see who to ask, never the credential.

        Attributes:
            connector_id (str):
            pod_connector (str):
            account_id (None | Unset | UUID):
            connected_by (None | SurfaceConnectionOwner | Unset):
            display_name (None | str | Unset):
            status (SurfaceConnectionStatus | Unset): Health of the account a surface runs on.

                Mirrors ``AccountStatus`` and adds ``MISSING`` for a surface whose pod
                connector runs as no account right now: removed, or its account withdrawn
                by its owner or gone. Whether the owner is still in the pod
                is deliberately *not* folded in here: a departed owner's token keeps working
                until it expires, so it is a separate fact (``connected_by.is_pod_member``),
                not a rung on this ladder.
    """

    connector_id: str
    pod_connector: str
    account_id: None | Unset | UUID = UNSET
    connected_by: None | SurfaceConnectionOwner | Unset = UNSET
    display_name: None | str | Unset = UNSET
    status: SurfaceConnectionStatus | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.surface_connection_owner import SurfaceConnectionOwner

        connector_id = self.connector_id

        pod_connector = self.pod_connector

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        connected_by: dict[str, Any] | None | Unset
        if isinstance(self.connected_by, Unset):
            connected_by = UNSET
        elif isinstance(self.connected_by, SurfaceConnectionOwner):
            connected_by = self.connected_by.to_dict()
        else:
            connected_by = self.connected_by

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
                "pod_connector": pod_connector,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if connected_by is not UNSET:
            field_dict["connected_by"] = connected_by
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_connection_owner import SurfaceConnectionOwner

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        pod_connector = d.pop("pod_connector")

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_connected_by(data: object) -> None | SurfaceConnectionOwner | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                connected_by_type_0 = SurfaceConnectionOwner.from_dict(data)

                return connected_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SurfaceConnectionOwner | Unset, data)

        connected_by = _parse_connected_by(d.pop("connected_by", UNSET))

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        _status = d.pop("status", UNSET)
        status: SurfaceConnectionStatus | Unset
        if isinstance(_status, Unset):
            status = UNSET
        else:
            status = SurfaceConnectionStatus(_status)

        surface_connection = cls(
            connector_id=connector_id,
            pod_connector=pod_connector,
            account_id=account_id,
            connected_by=connected_by,
            display_name=display_name,
            status=status,
        )

        surface_connection.additional_properties = d
        return surface_connection

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
