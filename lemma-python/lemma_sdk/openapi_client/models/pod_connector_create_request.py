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
        auth_config_id (None | Unset | UUID): The organization installation this connector uses. Give this or
            `connector_id`.
        connector_id (None | str | Unset): A catalog connector (`gmail`) instead of an installation: its organization
            default, or -- when the organization has none and you may install connectors -- Lemma's own app for it,
            installed now.
        mode (PodConnectorMode | Unset):
        purpose (None | str | Unset): Shown to members when they are asked to connect.
        required (bool | Unset): PER_MEMBER: ask every member who has no account to connect one. Default: False.
        shared_pod_account_id (None | Unset | UUID): SHARED only: a pod account its owner has already shared with the
            pod.
        slug (None | str | Unset): Pod-local name apps, agents and bundles address it by, e.g. `gmail` or `support-
            inbox`. Left out: the installation's name, made free in this pod.
        visibility (PodConnectorVisibility | Unset):
    """

    auth_config_id: None | Unset | UUID = UNSET
    connector_id: None | str | Unset = UNSET
    mode: PodConnectorMode | Unset = UNSET
    purpose: None | str | Unset = UNSET
    required: bool | Unset = False
    shared_pod_account_id: None | Unset | UUID = UNSET
    slug: None | str | Unset = UNSET
    visibility: PodConnectorVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_config_id: None | str | Unset
        if isinstance(self.auth_config_id, Unset):
            auth_config_id = UNSET
        elif isinstance(self.auth_config_id, UUID):
            auth_config_id = str(self.auth_config_id)
        else:
            auth_config_id = self.auth_config_id

        connector_id: None | str | Unset
        if isinstance(self.connector_id, Unset):
            connector_id = UNSET
        else:
            connector_id = self.connector_id

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

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

        slug: None | str | Unset
        if isinstance(self.slug, Unset):
            slug = UNSET
        else:
            slug = self.slug

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if auth_config_id is not UNSET:
            field_dict["auth_config_id"] = auth_config_id
        if connector_id is not UNSET:
            field_dict["connector_id"] = connector_id
        if mode is not UNSET:
            field_dict["mode"] = mode
        if purpose is not UNSET:
            field_dict["purpose"] = purpose
        if required is not UNSET:
            field_dict["required"] = required
        if shared_pod_account_id is not UNSET:
            field_dict["shared_pod_account_id"] = shared_pod_account_id
        if slug is not UNSET:
            field_dict["slug"] = slug
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_auth_config_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                auth_config_id_type_0 = UUID(data)

                return auth_config_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        auth_config_id = _parse_auth_config_id(d.pop("auth_config_id", UNSET))

        def _parse_connector_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_id = _parse_connector_id(d.pop("connector_id", UNSET))

        _mode = d.pop("mode", UNSET)
        mode: PodConnectorMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = PodConnectorMode(_mode)

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

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: PodConnectorVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = PodConnectorVisibility(_visibility)

        pod_connector_create_request = cls(
            auth_config_id=auth_config_id,
            connector_id=connector_id,
            mode=mode,
            purpose=purpose,
            required=required,
            shared_pod_account_id=shared_pod_account_id,
            slug=slug,
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
