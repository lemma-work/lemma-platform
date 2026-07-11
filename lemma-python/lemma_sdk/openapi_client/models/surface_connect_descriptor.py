from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.auth_scheme import AuthScheme
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_connect_descriptor_auth_config_schema_type_0 import (
        SurfaceConnectDescriptorAuthConfigSchemaType0,
    )
    from ..models.surface_connect_descriptor_credential_schema_type_0 import (
        SurfaceConnectDescriptorCredentialSchemaType0,
    )


T = TypeVar("T", bound="SurfaceConnectDescriptor")


@_attrs_define
class SurfaceConnectDescriptor:
    """What the frontend needs to render the "connect an account" (CUSTOM) flow
    for a surface's connector — a slim projection of the connector's LEMMA
    capability. ``system_oauth_available`` means the platform supplies the OAuth
    app so the user connects without registering their own (distinct from whether
    a fully-managed SYSTEM bot exists — that's ``supported_credential_modes``).

        Attributes:
            auth_scheme (AuthScheme):
            auth_config_schema (None | SurfaceConnectDescriptorAuthConfigSchemaType0 | Unset):
            credential_schema (None | SurfaceConnectDescriptorCredentialSchemaType0 | Unset):
            supports_org_custom_oauth (bool | Unset):  Default: False.
            system_oauth_available (bool | Unset):  Default: False.
    """

    auth_scheme: AuthScheme
    auth_config_schema: None | SurfaceConnectDescriptorAuthConfigSchemaType0 | Unset = (
        UNSET
    )
    credential_schema: None | SurfaceConnectDescriptorCredentialSchemaType0 | Unset = (
        UNSET
    )
    supports_org_custom_oauth: bool | Unset = False
    system_oauth_available: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.surface_connect_descriptor_auth_config_schema_type_0 import (
            SurfaceConnectDescriptorAuthConfigSchemaType0,
        )
        from ..models.surface_connect_descriptor_credential_schema_type_0 import (
            SurfaceConnectDescriptorCredentialSchemaType0,
        )

        auth_scheme = self.auth_scheme.value

        auth_config_schema: dict[str, Any] | None | Unset
        if isinstance(self.auth_config_schema, Unset):
            auth_config_schema = UNSET
        elif isinstance(
            self.auth_config_schema, SurfaceConnectDescriptorAuthConfigSchemaType0
        ):
            auth_config_schema = self.auth_config_schema.to_dict()
        else:
            auth_config_schema = self.auth_config_schema

        credential_schema: dict[str, Any] | None | Unset
        if isinstance(self.credential_schema, Unset):
            credential_schema = UNSET
        elif isinstance(
            self.credential_schema, SurfaceConnectDescriptorCredentialSchemaType0
        ):
            credential_schema = self.credential_schema.to_dict()
        else:
            credential_schema = self.credential_schema

        supports_org_custom_oauth = self.supports_org_custom_oauth

        system_oauth_available = self.system_oauth_available

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_scheme": auth_scheme,
            }
        )
        if auth_config_schema is not UNSET:
            field_dict["auth_config_schema"] = auth_config_schema
        if credential_schema is not UNSET:
            field_dict["credential_schema"] = credential_schema
        if supports_org_custom_oauth is not UNSET:
            field_dict["supports_org_custom_oauth"] = supports_org_custom_oauth
        if system_oauth_available is not UNSET:
            field_dict["system_oauth_available"] = system_oauth_available

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_connect_descriptor_auth_config_schema_type_0 import (
            SurfaceConnectDescriptorAuthConfigSchemaType0,
        )
        from ..models.surface_connect_descriptor_credential_schema_type_0 import (
            SurfaceConnectDescriptorCredentialSchemaType0,
        )

        d = dict(src_dict)
        auth_scheme = AuthScheme(d.pop("auth_scheme"))

        def _parse_auth_config_schema(
            data: object,
        ) -> None | SurfaceConnectDescriptorAuthConfigSchemaType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                auth_config_schema_type_0 = (
                    SurfaceConnectDescriptorAuthConfigSchemaType0.from_dict(data)
                )

                return auth_config_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(
                None | SurfaceConnectDescriptorAuthConfigSchemaType0 | Unset, data
            )

        auth_config_schema = _parse_auth_config_schema(
            d.pop("auth_config_schema", UNSET)
        )

        def _parse_credential_schema(
            data: object,
        ) -> None | SurfaceConnectDescriptorCredentialSchemaType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_schema_type_0 = (
                    SurfaceConnectDescriptorCredentialSchemaType0.from_dict(data)
                )

                return credential_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(
                None | SurfaceConnectDescriptorCredentialSchemaType0 | Unset, data
            )

        credential_schema = _parse_credential_schema(d.pop("credential_schema", UNSET))

        supports_org_custom_oauth = d.pop("supports_org_custom_oauth", UNSET)

        system_oauth_available = d.pop("system_oauth_available", UNSET)

        surface_connect_descriptor = cls(
            auth_scheme=auth_scheme,
            auth_config_schema=auth_config_schema,
            credential_schema=credential_schema,
            supports_org_custom_oauth=supports_org_custom_oauth,
            system_oauth_available=system_oauth_available,
        )

        surface_connect_descriptor.additional_properties = d
        return surface_connect_descriptor

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
