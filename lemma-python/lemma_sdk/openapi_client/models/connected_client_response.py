from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.scope import Scope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_subscription_response import EventSubscriptionResponse


T = TypeVar("T", bound="ConnectedClientResponse")


@_attrs_define
class ConnectedClientResponse:
    """
    Attributes:
        client_id (str):
        client_name (str):
        client_uri (None | str):
        connected_at (datetime.datetime):
        grant_id (UUID):
        last_used_at (datetime.datetime | None):
        pod_id (UUID):
        scopes (list[Scope]):
        user_id (UUID): The person who connected it.
        listens_to (list[EventSubscriptionResponse] | Unset):
    """

    client_id: str
    client_name: str
    client_uri: None | str
    connected_at: datetime.datetime
    grant_id: UUID
    last_used_at: datetime.datetime | None
    pod_id: UUID
    scopes: list[Scope]
    user_id: UUID
    listens_to: list[EventSubscriptionResponse] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        client_name = self.client_name

        client_uri: None | str
        client_uri = self.client_uri

        connected_at = self.connected_at.isoformat()

        grant_id = str(self.grant_id)

        last_used_at: None | str
        if isinstance(self.last_used_at, datetime.datetime):
            last_used_at = self.last_used_at.isoformat()
        else:
            last_used_at = self.last_used_at

        pod_id = str(self.pod_id)

        scopes = []
        for scopes_item_data in self.scopes:
            scopes_item = scopes_item_data.value
            scopes.append(scopes_item)

        user_id = str(self.user_id)

        listens_to: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.listens_to, Unset):
            listens_to = []
            for listens_to_item_data in self.listens_to:
                listens_to_item = listens_to_item_data.to_dict()
                listens_to.append(listens_to_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "client_id": client_id,
                "client_name": client_name,
                "client_uri": client_uri,
                "connected_at": connected_at,
                "grant_id": grant_id,
                "last_used_at": last_used_at,
                "pod_id": pod_id,
                "scopes": scopes,
                "user_id": user_id,
            }
        )
        if listens_to is not UNSET:
            field_dict["listens_to"] = listens_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_subscription_response import EventSubscriptionResponse

        d = dict(src_dict)
        client_id = d.pop("client_id")

        client_name = d.pop("client_name")

        def _parse_client_uri(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        client_uri = _parse_client_uri(d.pop("client_uri"))

        connected_at = isoparse(d.pop("connected_at"))

        grant_id = UUID(d.pop("grant_id"))

        def _parse_last_used_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_used_at_type_0 = isoparse(data)

                return last_used_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_used_at = _parse_last_used_at(d.pop("last_used_at"))

        pod_id = UUID(d.pop("pod_id"))

        scopes = []
        _scopes = d.pop("scopes")
        for scopes_item_data in _scopes:
            scopes_item = Scope(scopes_item_data)

            scopes.append(scopes_item)

        user_id = UUID(d.pop("user_id"))

        _listens_to = d.pop("listens_to", UNSET)
        listens_to: list[EventSubscriptionResponse] | Unset = UNSET
        if _listens_to is not UNSET:
            listens_to = []
            for listens_to_item_data in _listens_to:
                listens_to_item = EventSubscriptionResponse.from_dict(
                    listens_to_item_data
                )

                listens_to.append(listens_to_item)

        connected_client_response = cls(
            client_id=client_id,
            client_name=client_name,
            client_uri=client_uri,
            connected_at=connected_at,
            grant_id=grant_id,
            last_used_at=last_used_at,
            pod_id=pod_id,
            scopes=scopes,
            user_id=user_id,
            listens_to=listens_to,
        )

        connected_client_response.additional_properties = d
        return connected_client_response

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
