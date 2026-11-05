from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.pod_account_source import PodAccountSource
from ..models.pod_account_visibility import PodAccountVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodAccountResponse")


@_attrs_define
class PodAccountResponse:
    """
    Attributes:
        account_id (UUID):
        auth_config_id (UUID):
        created_at (datetime.datetime):
        id (UUID):
        is_member_default (bool):
        owner_user_id (UUID):
        pod_id (UUID):
        source (PodAccountSource):
        visibility (PodAccountVisibility):
        allow_sandbox_use (bool | Unset):  Default: False.
        connector_id (None | str | Unset):
        display_name (None | str | Unset):
        email (None | str | Unset):
        last_used_at (datetime.datetime | None | Unset):
        notice (None | str | Unset): Something the change did not do at once, for the owner to read: after turning
            `allow_sandbox_use` off, that sandboxes already given the token keep a copy until it is swept.
        used_by (list[str] | Unset): Slugs of the SHARED pod connectors that run as this account.
    """

    account_id: UUID
    auth_config_id: UUID
    created_at: datetime.datetime
    id: UUID
    is_member_default: bool
    owner_user_id: UUID
    pod_id: UUID
    source: PodAccountSource
    visibility: PodAccountVisibility
    allow_sandbox_use: bool | Unset = False
    connector_id: None | str | Unset = UNSET
    display_name: None | str | Unset = UNSET
    email: None | str | Unset = UNSET
    last_used_at: datetime.datetime | None | Unset = UNSET
    notice: None | str | Unset = UNSET
    used_by: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id = str(self.account_id)

        auth_config_id = str(self.auth_config_id)

        created_at = self.created_at.isoformat()

        id = str(self.id)

        is_member_default = self.is_member_default

        owner_user_id = str(self.owner_user_id)

        pod_id = str(self.pod_id)

        source = self.source.value

        visibility = self.visibility.value

        allow_sandbox_use = self.allow_sandbox_use

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        email: None | str | Unset
        if isinstance(self.email, Unset):
            email = UNSET
        else:
            email = self.email

        last_used_at: None | str | Unset
        if isinstance(self.last_used_at, Unset):
            last_used_at = UNSET
        elif isinstance(self.last_used_at, datetime.datetime):
            last_used_at = self.last_used_at.isoformat()
        else:
            last_used_at = self.last_used_at

        notice: None | str | Unset
        if isinstance(self.notice, Unset):
            notice = UNSET
        else:
            notice = self.notice

        used_by: list[str] | Unset = UNSET
        if not isinstance(self.used_by, Unset):
            used_by = self.used_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
                "auth_config_id": auth_config_id,
                "created_at": created_at,
                "id": id,
                "is_member_default": is_member_default,
                "owner_user_id": owner_user_id,
                "pod_id": pod_id,
                "source": source,
                "visibility": visibility,
            }
        )
        if allow_sandbox_use is not UNSET:
            field_dict["allow_sandbox_use"] = allow_sandbox_use
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if email is not UNSET:
            field_dict["email"] = email
        if last_used_at is not UNSET:
            field_dict["last_used_at"] = last_used_at
        if notice is not UNSET:
            field_dict["notice"] = notice
        if used_by is not UNSET:
            field_dict["used_by"] = used_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_id = UUID(d.pop("account_id"))

        auth_config_id = UUID(d.pop("auth_config_id"))

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        is_member_default = d.pop("is_member_default")

        owner_user_id = UUID(d.pop("owner_user_id"))

        pod_id = UUID(d.pop("pod_id"))

        source = PodAccountSource(d.pop("source"))

        visibility = PodAccountVisibility(d.pop("visibility"))

        allow_sandbox_use = d.pop("allow_sandbox_use", UNSET)

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        def _parse_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email = _parse_email(d.pop("email", UNSET))

        def _parse_last_used_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_used_at_type_0 = isoparse(data)

                return last_used_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_used_at = _parse_last_used_at(d.pop("last_used_at", UNSET))

        def _parse_notice(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notice = _parse_notice(d.pop("notice", UNSET))

        used_by = cast(list[str], d.pop("used_by", UNSET))

        pod_account_response = cls(
            account_id=account_id,
            auth_config_id=auth_config_id,
            created_at=created_at,
            id=id,
            is_member_default=is_member_default,
            owner_user_id=owner_user_id,
            pod_id=pod_id,
            source=source,
            visibility=visibility,
            allow_sandbox_use=allow_sandbox_use,
            connector_id=connector_id,
            display_name=display_name,
            email=email,
            last_used_at=last_used_at,
            notice=notice,
            used_by=used_by,
        )

        pod_account_response.additional_properties = d
        return pod_account_response

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
