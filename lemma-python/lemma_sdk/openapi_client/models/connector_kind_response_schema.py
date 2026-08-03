from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.auth_scheme import AuthScheme
from ..models.connector_kind import ConnectorKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_kind_response_schema_config_schema_type_0 import (
        ConnectorKindResponseSchemaConfigSchemaType0,
    )
    from ..models.connector_kind_response_schema_credential_schema_type_0 import (
        ConnectorKindResponseSchemaCredentialSchemaType0,
    )
    from ..models.o_auth_2_defaults_response_schema import OAuth2DefaultsResponseSchema


T = TypeVar("T", bound="ConnectorKindResponseSchema")


@_attrs_define
class ConnectorKindResponseSchema:
    """One way a connector can be installed.

    Flat rather than a union over the five kinds: a client's job here is to
    decide what to put in an install's `config`, and for that `kind` plus the
    schemas is the whole answer. The per-kind extras below are populated only
    where they apply.

        Attributes:
            kind (ConnectorKind): How an install authenticates, discovers and executes operations.
            auth_scheme (AuthScheme | Unset):
            config_schema (ConnectorKindResponseSchemaConfigSchemaType0 | None | Unset): JSON Schema for an install's
                `config`.
            credential_schema (ConnectorKindResponseSchemaCredentialSchemaType0 | None | Unset):
            discovery (str | Unset):  Default: 'none'.
            oauth2_defaults (None | OAuth2DefaultsResponseSchema | Unset):
            package_name (None | str | Unset):
            supports_org_custom_auth_config (bool | Unset):  Default: False.
            supports_org_custom_oauth (bool | Unset):  Default: False.
            system_default_available (bool | Unset):  Default: False.
            toolkit_slug (None | str | Unset):
    """

    kind: ConnectorKind
    auth_scheme: AuthScheme | Unset = UNSET
    config_schema: ConnectorKindResponseSchemaConfigSchemaType0 | None | Unset = UNSET
    credential_schema: (
        ConnectorKindResponseSchemaCredentialSchemaType0 | None | Unset
    ) = UNSET
    discovery: str | Unset = "none"
    oauth2_defaults: None | OAuth2DefaultsResponseSchema | Unset = UNSET
    package_name: None | str | Unset = UNSET
    supports_org_custom_auth_config: bool | Unset = False
    supports_org_custom_oauth: bool | Unset = False
    system_default_available: bool | Unset = False
    toolkit_slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.connector_kind_response_schema_config_schema_type_0 import (
            ConnectorKindResponseSchemaConfigSchemaType0,
        )
        from ..models.connector_kind_response_schema_credential_schema_type_0 import (
            ConnectorKindResponseSchemaCredentialSchemaType0,
        )
        from ..models.o_auth_2_defaults_response_schema import (
            OAuth2DefaultsResponseSchema,
        )

        kind = self.kind.value

        auth_scheme: str | Unset = UNSET
        if not isinstance(self.auth_scheme, Unset):
            auth_scheme = self.auth_scheme.value

        config_schema: dict[str, Any] | None | Unset
        if isinstance(self.config_schema, Unset):
            config_schema = UNSET
        elif isinstance(
            self.config_schema, ConnectorKindResponseSchemaConfigSchemaType0
        ):
            config_schema = self.config_schema.to_dict()
        else:
            config_schema = self.config_schema

        credential_schema: dict[str, Any] | None | Unset
        if isinstance(self.credential_schema, Unset):
            credential_schema = UNSET
        elif isinstance(
            self.credential_schema, ConnectorKindResponseSchemaCredentialSchemaType0
        ):
            credential_schema = self.credential_schema.to_dict()
        else:
            credential_schema = self.credential_schema

        discovery = self.discovery

        oauth2_defaults: dict[str, Any] | None | Unset
        if isinstance(self.oauth2_defaults, Unset):
            oauth2_defaults = UNSET
        elif isinstance(self.oauth2_defaults, OAuth2DefaultsResponseSchema):
            oauth2_defaults = self.oauth2_defaults.to_dict()
        else:
            oauth2_defaults = self.oauth2_defaults

        package_name: None | str | Unset
        if isinstance(self.package_name, Unset):
            package_name = UNSET
        else:
            package_name = self.package_name

        supports_org_custom_auth_config = self.supports_org_custom_auth_config

        supports_org_custom_oauth = self.supports_org_custom_oauth

        system_default_available = self.system_default_available

        toolkit_slug: None | str | Unset
        if isinstance(self.toolkit_slug, Unset):
            toolkit_slug = UNSET
        else:
            toolkit_slug = self.toolkit_slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
            }
        )
        if auth_scheme is not UNSET:
            field_dict["auth_scheme"] = auth_scheme
        if config_schema is not UNSET:
            field_dict["config_schema"] = config_schema
        if credential_schema is not UNSET:
            field_dict["credential_schema"] = credential_schema
        if discovery is not UNSET:
            field_dict["discovery"] = discovery
        if oauth2_defaults is not UNSET:
            field_dict["oauth2_defaults"] = oauth2_defaults
        if package_name is not UNSET:
            field_dict["package_name"] = package_name
        if supports_org_custom_auth_config is not UNSET:
            field_dict["supports_org_custom_auth_config"] = (
                supports_org_custom_auth_config
            )
        if supports_org_custom_oauth is not UNSET:
            field_dict["supports_org_custom_oauth"] = supports_org_custom_oauth
        if system_default_available is not UNSET:
            field_dict["system_default_available"] = system_default_available
        if toolkit_slug is not UNSET:
            field_dict["toolkit_slug"] = toolkit_slug

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_kind_response_schema_config_schema_type_0 import (
            ConnectorKindResponseSchemaConfigSchemaType0,
        )
        from ..models.connector_kind_response_schema_credential_schema_type_0 import (
            ConnectorKindResponseSchemaCredentialSchemaType0,
        )
        from ..models.o_auth_2_defaults_response_schema import (
            OAuth2DefaultsResponseSchema,
        )

        d = dict(src_dict)
        kind = ConnectorKind(d.pop("kind"))

        _auth_scheme = d.pop("auth_scheme", UNSET)
        auth_scheme: AuthScheme | Unset
        if isinstance(_auth_scheme, Unset):
            auth_scheme = UNSET
        else:
            auth_scheme = AuthScheme(_auth_scheme)

        def _parse_config_schema(
            data: object,
        ) -> ConnectorKindResponseSchemaConfigSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_schema_type_0 = (
                    ConnectorKindResponseSchemaConfigSchemaType0.from_dict(data)
                )

                return config_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                ConnectorKindResponseSchemaConfigSchemaType0 | None | Unset, data
            )

        config_schema = _parse_config_schema(d.pop("config_schema", UNSET))

        def _parse_credential_schema(
            data: object,
        ) -> ConnectorKindResponseSchemaCredentialSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_schema_type_0 = (
                    ConnectorKindResponseSchemaCredentialSchemaType0.from_dict(data)
                )

                return credential_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                ConnectorKindResponseSchemaCredentialSchemaType0 | None | Unset, data
            )

        credential_schema = _parse_credential_schema(d.pop("credential_schema", UNSET))

        discovery = d.pop("discovery", UNSET)

        def _parse_oauth2_defaults(
            data: object,
        ) -> None | OAuth2DefaultsResponseSchema | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                oauth2_defaults_type_0 = OAuth2DefaultsResponseSchema.from_dict(data)

                return oauth2_defaults_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | OAuth2DefaultsResponseSchema | Unset, data)

        oauth2_defaults = _parse_oauth2_defaults(d.pop("oauth2_defaults", UNSET))

        def _parse_package_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        package_name = _parse_package_name(d.pop("package_name", UNSET))

        supports_org_custom_auth_config = d.pop(
            "supports_org_custom_auth_config", UNSET
        )

        supports_org_custom_oauth = d.pop("supports_org_custom_oauth", UNSET)

        system_default_available = d.pop("system_default_available", UNSET)

        def _parse_toolkit_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        toolkit_slug = _parse_toolkit_slug(d.pop("toolkit_slug", UNSET))

        connector_kind_response_schema = cls(
            kind=kind,
            auth_scheme=auth_scheme,
            config_schema=config_schema,
            credential_schema=credential_schema,
            discovery=discovery,
            oauth2_defaults=oauth2_defaults,
            package_name=package_name,
            supports_org_custom_auth_config=supports_org_custom_auth_config,
            supports_org_custom_oauth=supports_org_custom_oauth,
            system_default_available=system_default_available,
            toolkit_slug=toolkit_slug,
        )

        connector_kind_response_schema.additional_properties = d
        return connector_kind_response_schema

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
