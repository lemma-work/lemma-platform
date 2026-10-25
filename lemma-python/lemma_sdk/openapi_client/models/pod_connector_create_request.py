from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_connector_mode import PodConnectorMode
from ..models.pod_connector_visibility import PodConnectorVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodConnectorCreateRequest")


@_attrs_define
class PodConnectorCreateRequest:
    """
    Attributes:
        auth_config_id (UUID): The organization installation this connector uses.
        mode (PodConnectorMode):
        slug (str): Pod-local name apps, agents and bundles address it by, e.g. `gmail` or `support-inbox`.
        purpose (None | str | Unset): Shown to members when they are asked to connect.
        required (bool | Unset): PER_MEMBER: ask every member who has no account to connect one. Default: False.
        shared_pod_account_id (None | Unset | UUID): SHARED only: a pod account its owner has already shared with the
            pod.
        visibility (PodConnectorVisibility | Unset):
    """

    auth_config_id: UUID
    mode: PodConnectorMode
    slug: str
    purpose: None | str | Unset = UNSET
    required: bool | Unset = False
    shared_pod_account_id: None | Unset | UUID = UNSET
    visibility: PodConnectorVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_config_id = str(self.auth_config_id)

        mode = self.mode.value

        slug = self.slug

        purpose: None | str | Unset
        if isinstance(self.purpose, Unset):
            purpose = UNSET
        else:
            purpose = self.purpose

        required = self.required

        shared_pod_account_id: None | str | Unset
        if isinstance(self.shared_pod_account_id, Unset):
            shared_pod_account_id = UNSET
        elif isinstance(self.shared_pod_account_id, UUID):
            shared_pod_account_id = str(self.shared_pod_account_id)
        else:
            shared_pod_account_id = self.shared_pod_account_id

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_config_id": auth_config_id,
                "mode": mode,
                "slug": slug,
            }
        )
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
        auth_config_id = UUID(d.pop("auth_config_id"))

        mode = PodConnectorMode(d.pop("mode"))

        slug = d.pop("slug")

        def _parse_purpose(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        purpose = _parse_purpose(d.pop("purpose", UNSET))

        required = d.pop("required", UNSET)

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

        _visibility = d.pop("visibility", UNSET)
        visibility: PodConnectorVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = PodConnectorVisibility(_visibility)

        pod_connector_create_request = cls(
            auth_config_id=auth_config_id,
            mode=mode,
            slug=slug,
            purpose=purpose,
            required=required,
            shared_pod_account_id=shared_pod_account_id,
            visibility=visibility,
        )

        pod_connector_create_request.additional_properties = d
        return pod_connector_create_request

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
