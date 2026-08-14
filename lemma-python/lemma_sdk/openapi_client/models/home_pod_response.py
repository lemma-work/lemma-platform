from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.home_agent_response import HomeAgentResponse
    from ..models.home_app_response import HomeAppResponse


T = TypeVar("T", bound="HomePodResponse")


@_attrs_define
class HomePodResponse:
    """A pod with what it contains and what the caller is to it.

    Attributes:
        agents (list[HomeAgentResponse]):
        apps (list[HomeAppResponse]):
        id (UUID):
        name (str):
        roles (list[str]):
        description (None | str | Unset):
        icon_url (None | str | Unset):
    """

    agents: list[HomeAgentResponse]
    apps: list[HomeAppResponse]
    id: UUID
    name: str
    roles: list[str]
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents = []
        for agents_item_data in self.agents:
            agents_item = agents_item_data.to_dict()
            agents.append(agents_item)

        apps = []
        for apps_item_data in self.apps:
            apps_item = apps_item_data.to_dict()
            apps.append(apps_item)

        id = str(self.id)

        name = self.name

        roles = self.roles

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agents": agents,
                "apps": apps,
                "id": id,
                "name": name,
                "roles": roles,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.home_agent_response import HomeAgentResponse
        from ..models.home_app_response import HomeAppResponse

        d = dict(src_dict)
        agents = []
        _agents = d.pop("agents")
        for agents_item_data in _agents:
            agents_item = HomeAgentResponse.from_dict(agents_item_data)

            agents.append(agents_item)

        apps = []
        _apps = d.pop("apps")
        for apps_item_data in _apps:
            apps_item = HomeAppResponse.from_dict(apps_item_data)

            apps.append(apps_item)

        id = UUID(d.pop("id"))

        name = d.pop("name")

        roles = cast(list[str], d.pop("roles"))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        home_pod_response = cls(
            agents=agents,
            apps=apps,
            id=id,
            name=name,
            roles=roles,
            description=description,
            icon_url=icon_url,
        )

        home_pod_response.additional_properties = d
        return home_pod_response

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
