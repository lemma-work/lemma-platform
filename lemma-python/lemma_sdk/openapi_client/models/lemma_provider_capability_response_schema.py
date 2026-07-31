from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.auth_scheme import AuthScheme
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.lemma_provider_capability_response_schema_auth_config_schema_type_0 import (
        LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0,
    )
    from ..models.lemma_provider_capability_response_schema_credential_schema_type_0 import (
        LemmaProviderCapabilityResponseSchemaCredentialSchemaType0,
    )
    from ..models.o_auth_2_defaults_response_schema import OAuth2DefaultsResponseSchema


T = TypeVar("T", bound="LemmaProviderCapabilityResponseSchema")


@_attrs_define
class LemmaProviderCapabilityResponseSchema:
    """
    Attributes:
        auth_config_schema (LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset):
        auth_scheme (AuthScheme | Unset):
        credential_schema (LemmaProviderCapabilityResponseSchemaCredentialSchemaType0 | None | Unset):
        oauth2_defaults (None | OAuth2DefaultsResponseSchema | Unset):
        package_name (None | str | Unset):
        provider (Literal['LEMMA'] | Unset):  Default: 'LEMMA'.
        supports_org_custom_oauth (bool | Unset):  Default: False.
        system_default_available (bool | Unset):  Default: False.
    """

    auth_config_schema: (
        LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset
    ) = UNSET
    auth_scheme: AuthScheme | Unset = UNSET
    credential_schema: (
        LemmaProviderCapabilityResponseSchemaCredentialSchemaType0 | None | Unset
    ) = UNSET
    oauth2_defaults: None | OAuth2DefaultsResponseSchema | Unset = UNSET
    package_name: None | str | Unset = UNSET
    provider: Literal["LEMMA"] | Unset = "LEMMA"
    supports_org_custom_oauth: bool | Unset = False
    system_default_available: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.lemma_provider_capability_response_schema_auth_config_schema_type_0 import (
            LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        )
        from ..models.lemma_provider_capability_response_schema_credential_schema_type_0 import (
            LemmaProviderCapabilityResponseSchemaCredentialSchemaType0,
        )
        from ..models.o_auth_2_defaults_response_schema import (
            OAuth2DefaultsResponseSchema,
        )

        auth_config_schema: dict[str, Any] | None | Unset
        if isinstance(self.auth_config_schema, Unset):
            auth_config_schema = UNSET
        elif isinstance(
            self.auth_config_schema,
            LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        ):
            auth_config_schema = self.auth_config_schema.to_dict()
        else:
            auth_config_schema = self.auth_config_schema

        auth_scheme: str | Unset = UNSET
        if not isinstance(self.auth_scheme, Unset):
            auth_scheme = self.auth_scheme.value

        credential_schema: dict[str, Any] | None | Unset
        if isinstance(self.credential_schema, Unset):
            credential_schema = UNSET
        elif isinstance(
            self.credential_schema,
            LemmaProviderCapabilityResponseSchemaCredentialSchemaType0,
        ):
            credential_schema = self.credential_schema.to_dict()
        else:
            credential_schema = self.credential_schema

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

        provider = self.provider

        supports_org_custom_oauth = self.supports_org_custom_oauth

        system_default_available = self.system_default_available

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if auth_config_schema is not UNSET:
            field_dict["auth_config_schema"] = auth_config_schema
        if auth_scheme is not UNSET:
            field_dict["auth_scheme"] = auth_scheme
        if credential_schema is not UNSET:
            field_dict["credential_schema"] = credential_schema
        if oauth2_defaults is not UNSET:
            field_dict["oauth2_defaults"] = oauth2_defaults
        if package_name is not UNSET:
            field_dict["package_name"] = package_name
        if provider is not UNSET:
            field_dict["provider"] = provider
        if supports_org_custom_oauth is not UNSET:
            field_dict["supports_org_custom_oauth"] = supports_org_custom_oauth
        if system_default_available is not UNSET:
            field_dict["system_default_available"] = system_default_available

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.lemma_provider_capability_response_schema_auth_config_schema_type_0 import (
            LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        )
        from ..models.lemma_provider_capability_response_schema_credential_schema_type_0 import (
            LemmaProviderCapabilityResponseSchemaCredentialSchemaType0,
        )
        from ..models.o_auth_2_defaults_response_schema import (
            OAuth2DefaultsResponseSchema,
        )

        d = dict(src_dict)

        def _parse_auth_config_schema(
            data: object,
        ) -> LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                auth_config_schema_type_0 = LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0.from_dict(
                    data
                )

                return auth_config_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                LemmaProviderCapabilityResponseSchemaAuthConfigSchemaType0
                | None
                | Unset,
                data,
            )

        auth_config_schema = _parse_auth_config_schema(
            d.pop("auth_config_schema", UNSET)
        )

        _auth_scheme = d.pop("auth_scheme", UNSET)
        auth_scheme: AuthScheme | Unset
        if isinstance(_auth_scheme, Unset):
            auth_scheme = UNSET
        else:
            auth_scheme = AuthScheme(_auth_scheme)

        def _parse_credential_schema(
            data: object,
        ) -> LemmaProviderCapabilityResponseSchemaCredentialSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_schema_type_0 = LemmaProviderCapabilityResponseSchemaCredentialSchemaType0.from_dict(
                    data
                )

                return credential_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                LemmaProviderCapabilityResponseSchemaCredentialSchemaType0
                | None
                | Unset,
                data,
            )

        credential_schema = _parse_credential_schema(d.pop("credential_schema", UNSET))

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

        provider = cast(Literal["LEMMA"] | Unset, d.pop("provider", UNSET))
        if provider != "LEMMA" and not isinstance(provider, Unset):
            raise ValueError(f"provider must match const 'LEMMA', got '{provider}'")

        supports_org_custom_oauth = d.pop("supports_org_custom_oauth", UNSET)

        system_default_available = d.pop("system_default_available", UNSET)

        lemma_provider_capability_response_schema = cls(
            auth_config_schema=auth_config_schema,
            auth_scheme=auth_scheme,
            credential_schema=credential_schema,
            oauth2_defaults=oauth2_defaults,
            package_name=package_name,
            provider=provider,
            supports_org_custom_oauth=supports_org_custom_oauth,
            system_default_available=system_default_available,
        )

        lemma_provider_capability_response_schema.additional_properties = d
        return lemma_provider_capability_response_schema

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
