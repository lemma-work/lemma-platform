from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.machine_connection_response_capabilities import (
        MachineConnectionResponseCapabilities,
    )


T = TypeVar("T", bound="MachineConnectionResponse")


@_attrs_define
class MachineConnectionResponse:
    """
    Attributes:
        display_name (str): What the machine called itself.
        id (UUID):
        mode (str): `host` (as your OS user) or `image` (Docker).
        name (str): How refs name it: `mine:<name>`.
        online (bool): Whether its link is up right now.
        capabilities (MachineConnectionResponseCapabilities | Unset): What the machine reported it can do.
        created_at (datetime.datetime | None | Unset):
        home (None | str | Unset): Where Lemma's files live on the machine.
        last_seen_at (datetime.datetime | None | Unset):
        platform (None | str | Unset):
        pod_id (None | Unset | UUID): The pod it is shared into.
        runtime_version (None | str | Unset):
    """

    display_name: str
    id: UUID
    mode: str
    name: str
    online: bool
    capabilities: MachineConnectionResponseCapabilities | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    home: None | str | Unset = UNSET
    last_seen_at: datetime.datetime | None | Unset = UNSET
    platform: None | str | Unset = UNSET
    pod_id: None | Unset | UUID = UNSET
    runtime_version: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name = self.display_name

        id = str(self.id)

        mode = self.mode

        name = self.name

        online = self.online

        capabilities: dict[str, Any] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = self.capabilities.to_dict()

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        home: None | str | Unset
        if isinstance(self.home, Unset):
            home = UNSET
        else:
            home = self.home

        last_seen_at: None | str | Unset
        if isinstance(self.last_seen_at, Unset):
            last_seen_at = UNSET
        elif isinstance(self.last_seen_at, datetime.datetime):
            last_seen_at = self.last_seen_at.isoformat()
        else:
            last_seen_at = self.last_seen_at

        platform: None | str | Unset
        if isinstance(self.platform, Unset):
            platform = UNSET
        else:
            platform = self.platform

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        runtime_version: None | str | Unset
        if isinstance(self.runtime_version, Unset):
            runtime_version = UNSET
        else:
            runtime_version = self.runtime_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "id": id,
                "mode": mode,
                "name": name,
                "online": online,
            }
        )
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if home is not UNSET:
            field_dict["home"] = home
        if last_seen_at is not UNSET:
            field_dict["last_seen_at"] = last_seen_at
        if platform is not UNSET:
            field_dict["platform"] = platform
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if runtime_version is not UNSET:
            field_dict["runtime_version"] = runtime_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.machine_connection_response_capabilities import (
            MachineConnectionResponseCapabilities,
        )

        d = dict(src_dict)
        display_name = d.pop("display_name")

        id = UUID(d.pop("id"))

        mode = d.pop("mode")

        name = d.pop("name")

        online = d.pop("online")

        _capabilities = d.pop("capabilities", UNSET)
        capabilities: MachineConnectionResponseCapabilities | Unset
        if isinstance(_capabilities, Unset):
            capabilities = UNSET
        else:
            capabilities = MachineConnectionResponseCapabilities.from_dict(
                _capabilities
            )

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_home(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        home = _parse_home(d.pop("home", UNSET))

        def _parse_last_seen_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_seen_at_type_0 = isoparse(data)

                return last_seen_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_seen_at = _parse_last_seen_at(d.pop("last_seen_at", UNSET))

        def _parse_platform(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        platform = _parse_platform(d.pop("platform", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        def _parse_runtime_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        runtime_version = _parse_runtime_version(d.pop("runtime_version", UNSET))

        machine_connection_response = cls(
            display_name=display_name,
            id=id,
            mode=mode,
            name=name,
            online=online,
            capabilities=capabilities,
            created_at=created_at,
            home=home,
            last_seen_at=last_seen_at,
            platform=platform,
            pod_id=pod_id,
            runtime_version=runtime_version,
        )

        machine_connection_response.additional_properties = d
        return machine_connection_response

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
