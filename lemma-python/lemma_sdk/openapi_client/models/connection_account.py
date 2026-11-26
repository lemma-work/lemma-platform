from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectionAccount")


@_attrs_define
class ConnectionAccount:
    """The caller's own account behind the connector here.

    Attributes:
        id (UUID): The organization account's id.
        label (None | str | Unset):
        pod_account_id (None | Unset | UUID):
    """

    id: UUID
    label: None | str | Unset = UNSET
    pod_account_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        pod_account_id: None | str | Unset
        if isinstance(self.pod_account_id, Unset):
            pod_account_id = UNSET
        elif isinstance(self.pod_account_id, UUID):
            pod_account_id = str(self.pod_account_id)
        else:
            pod_account_id = self.pod_account_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if label is not UNSET:
            field_dict["label"] = label
        if pod_account_id is not UNSET:
            field_dict["pod_account_id"] = pod_account_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_pod_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_account_id_type_0 = UUID(data)

                return pod_account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_account_id = _parse_pod_account_id(d.pop("pod_account_id", UNSET))

        connection_account = cls(
            id=id,
            label=label,
            pod_account_id=pod_account_id,
        )

        connection_account.additional_properties = d
        return connection_account

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
