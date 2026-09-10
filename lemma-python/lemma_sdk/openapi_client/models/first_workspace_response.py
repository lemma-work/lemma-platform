from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.first_workspace_response_entry import FirstWorkspaceResponseEntry
from ..types import UNSET, Unset

T = TypeVar("T", bound="FirstWorkspaceResponse")


@_attrs_define
class FirstWorkspaceResponse:
    """
    Attributes:
        entry (FirstWorkspaceResponseEntry):
        organization_created (bool):
        organization_id (UUID):
        pod_created (bool):
        assistant_id (None | Unset | UUID):
        pod_id (None | Unset | UUID):
    """

    entry: FirstWorkspaceResponseEntry
    organization_created: bool
    organization_id: UUID
    pod_created: bool
    assistant_id: None | Unset | UUID = UNSET
    pod_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        entry = self.entry.value

        organization_created = self.organization_created

        organization_id = str(self.organization_id)

        pod_created = self.pod_created

        assistant_id: None | str | Unset
        if isinstance(self.assistant_id, Unset):
            assistant_id = UNSET
        elif isinstance(self.assistant_id, UUID):
            assistant_id = str(self.assistant_id)
        else:
            assistant_id = self.assistant_id

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "entry": entry,
                "organization_created": organization_created,
                "organization_id": organization_id,
                "pod_created": pod_created,
            }
        )
        if assistant_id is not UNSET:
            field_dict["assistant_id"] = assistant_id
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        entry = FirstWorkspaceResponseEntry(d.pop("entry"))

        organization_created = d.pop("organization_created")

        organization_id = UUID(d.pop("organization_id"))

        pod_created = d.pop("pod_created")

        def _parse_assistant_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assistant_id_type_0 = UUID(data)

                return assistant_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        assistant_id = _parse_assistant_id(d.pop("assistant_id", UNSET))

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

        first_workspace_response = cls(
            entry=entry,
            organization_created=organization_created,
            organization_id=organization_id,
            pod_created=pod_created,
            assistant_id=assistant_id,
            pod_id=pod_id,
        )

        first_workspace_response.additional_properties = d
        return first_workspace_response

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
