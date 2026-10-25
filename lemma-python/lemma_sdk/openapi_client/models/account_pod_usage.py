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

T = TypeVar("T", bound="AccountPodUsage")


@_attrs_define
class AccountPodUsage:
    """
    Attributes:
        is_member_default (bool):
        pod_account_id (UUID):
        pod_id (UUID):
        source (PodAccountSource):
        visibility (PodAccountVisibility):
        last_used_at (datetime.datetime | None | Unset):
        used_by (list[str] | Unset):
    """

    is_member_default: bool
    pod_account_id: UUID
    pod_id: UUID
    source: PodAccountSource
    visibility: PodAccountVisibility
    last_used_at: datetime.datetime | None | Unset = UNSET
    used_by: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        is_member_default = self.is_member_default

        pod_account_id = str(self.pod_account_id)

        pod_id = str(self.pod_id)

        source = self.source.value

        visibility = self.visibility.value

        last_used_at: None | str | Unset
        if isinstance(self.last_used_at, Unset):
            last_used_at = UNSET
        elif isinstance(self.last_used_at, datetime.datetime):
            last_used_at = self.last_used_at.isoformat()
        else:
            last_used_at = self.last_used_at

        used_by: list[str] | Unset = UNSET
        if not isinstance(self.used_by, Unset):
            used_by = self.used_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "is_member_default": is_member_default,
                "pod_account_id": pod_account_id,
                "pod_id": pod_id,
                "source": source,
                "visibility": visibility,
            }
        )
        if last_used_at is not UNSET:
            field_dict["last_used_at"] = last_used_at
        if used_by is not UNSET:
            field_dict["used_by"] = used_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        is_member_default = d.pop("is_member_default")

        pod_account_id = UUID(d.pop("pod_account_id"))

        pod_id = UUID(d.pop("pod_id"))

        source = PodAccountSource(d.pop("source"))

        visibility = PodAccountVisibility(d.pop("visibility"))

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

        used_by = cast(list[str], d.pop("used_by", UNSET))

        account_pod_usage = cls(
            is_member_default=is_member_default,
            pod_account_id=pod_account_id,
            pod_id=pod_id,
            source=source,
            visibility=visibility,
            last_used_at=last_used_at,
            used_by=used_by,
        )

        account_pod_usage.additional_properties = d
        return account_pod_usage

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
