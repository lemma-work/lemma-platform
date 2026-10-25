from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_connector_mode import PodConnectorMode
from ..models.pod_connector_visibility import PodConnectorVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodConnectorUpdateRequest")


@_attrs_define
class PodConnectorUpdateRequest:
    """Fields left out are unchanged; `shared_pod_account_id: null` clears it.

    Attributes:
        mode (None | PodConnectorMode | Unset):
        purpose (None | str | Unset):
        required (bool | None | Unset):
        shared_pod_account_id (None | Unset | UUID):
        visibility (None | PodConnectorVisibility | Unset):
    """

    mode: None | PodConnectorMode | Unset = UNSET
    purpose: None | str | Unset = UNSET
    required: bool | None | Unset = UNSET
    shared_pod_account_id: None | Unset | UUID = UNSET
    visibility: None | PodConnectorVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        elif isinstance(self.mode, PodConnectorMode):
            mode = self.mode.value
        else:
            mode = self.mode

        purpose: None | str | Unset
        if isinstance(self.purpose, Unset):
            purpose = UNSET
        else:
            purpose = self.purpose

        required: bool | None | Unset
        if isinstance(self.required, Unset):
            required = UNSET
        else:
            required = self.required

        shared_pod_account_id: None | str | Unset
        if isinstance(self.shared_pod_account_id, Unset):
            shared_pod_account_id = UNSET
        elif isinstance(self.shared_pod_account_id, UUID):
            shared_pod_account_id = str(self.shared_pod_account_id)
        else:
            shared_pod_account_id = self.shared_pod_account_id

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, PodConnectorVisibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if mode is not UNSET:
            field_dict["mode"] = mode
        if purpose is not UNSET:
            field_dict["purpose"] = purpose
        if required is not UNSET:
            field_dict["required"] = required
        if shared_pod_account_id is not UNSET:
            field_dict["shared_pod_account_id"] = shared_pod_account_id
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_mode(data: object) -> None | PodConnectorMode | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                mode_type_0 = PodConnectorMode(data)

                return mode_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodConnectorMode | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_purpose(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        purpose = _parse_purpose(d.pop("purpose", UNSET))

        def _parse_required(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        required = _parse_required(d.pop("required", UNSET))

        def _parse_shared_pod_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                shared_pod_account_id_type_0 = UUID(data)

                return shared_pod_account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        shared_pod_account_id = _parse_shared_pod_account_id(
            d.pop("shared_pod_account_id", UNSET)
        )

        def _parse_visibility(data: object) -> None | PodConnectorVisibility | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = PodConnectorVisibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodConnectorVisibility | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        pod_connector_update_request = cls(
            mode=mode,
            purpose=purpose,
            required=required,
            shared_pod_account_id=shared_pod_account_id,
            visibility=visibility,
        )

        pod_connector_update_request.additional_properties = d
        return pod_connector_update_request

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
