from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.runs_as_kind import RunsAsKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConnectionRunsAs")


@_attrs_define
class ConnectionRunsAs:
    """Who a call by the caller runs as, in words the page can show.

    Attributes:
        kind (RunsAsKind):
        label (None | str | Unset): The caller's account label (me), the shared account's (person), or the App's GitHub
            organization login(s), comma-separated (app).
        owner_name (None | str | Unset):
        owner_user_id (None | Unset | UUID): SHARED: who shared the account.
    """

    kind: RunsAsKind
    label: None | str | Unset = UNSET
    owner_name: None | str | Unset = UNSET
    owner_user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        owner_name: None | str | Unset
        if isinstance(self.owner_name, Unset):
            owner_name = UNSET
        else:
            owner_name = self.owner_name

        owner_user_id: None | str | Unset
        if isinstance(self.owner_user_id, Unset):
            owner_user_id = UNSET
        elif isinstance(self.owner_user_id, UUID):
            owner_user_id = str(self.owner_user_id)
        else:
            owner_user_id = self.owner_user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
            }
        )
        if label is not UNSET:
            field_dict["label"] = label
        if owner_name is not UNSET:
            field_dict["owner_name"] = owner_name
        if owner_user_id is not UNSET:
            field_dict["owner_user_id"] = owner_user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = RunsAsKind(d.pop("kind"))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_owner_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        owner_name = _parse_owner_name(d.pop("owner_name", UNSET))

        def _parse_owner_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_user_id_type_0 = UUID(data)

                return owner_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        owner_user_id = _parse_owner_user_id(d.pop("owner_user_id", UNSET))

        connection_runs_as = cls(
            kind=kind,
            label=label,
            owner_name=owner_name,
            owner_user_id=owner_user_id,
        )

        connection_runs_as.additional_properties = d
        return connection_runs_as

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
