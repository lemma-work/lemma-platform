from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

T = TypeVar("T", bound="UserSurfaceItem")


@_attrs_define
class UserSurfaceItem:
    """One of the current user's surfaces (across any pod they belong to).

    Attributes:
        id (UUID):
        name (str):
        platform (SurfacePlatform): The platforms a pod can be reached on.

            Email is Resend, and only Resend. Gmail and Outlook were here as
            Composio-backed mailboxes, which made "an email surface" mean three
            different transports with three attachment strategies between them -- bytes,
            Graph drafts, and a signed URL the provider downloads server-side. Reaching
            a Gmail *account* is still something an agent does, through the connector;
            it is just not a surface.
        pod_id (UUID):
        agent_id (None | Unset | UUID):
        is_default (bool | Unset):  Default: False.
        shares_address (bool | Unset):  Default: False.
    """

    id: UUID
    name: str
    platform: SurfacePlatform
    pod_id: UUID
    agent_id: None | Unset | UUID = UNSET
    is_default: bool | Unset = False
    shares_address: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        name = self.name

        platform = self.platform.value

        pod_id = str(self.pod_id)

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        is_default = self.is_default

        shares_address = self.shares_address

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "platform": platform,
                "pod_id": pod_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if is_default is not UNSET:
            field_dict["is_default"] = is_default
        if shares_address is not UNSET:
            field_dict["shares_address"] = shares_address

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        platform = SurfacePlatform(d.pop("platform"))

        pod_id = UUID(d.pop("pod_id"))

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        is_default = d.pop("is_default", UNSET)

        shares_address = d.pop("shares_address", UNSET)

        user_surface_item = cls(
            id=id,
            name=name,
            platform=platform,
            pod_id=pod_id,
            agent_id=agent_id,
            is_default=is_default,
            shares_address=shares_address,
        )

        user_surface_item.additional_properties = d
        return user_surface_item

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
