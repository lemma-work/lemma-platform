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
    from ..models.composio_provider_capability_response_schema_auth_config_schema_type_0 import (
        ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0,
    )


T = TypeVar("T", bound="ComposioProviderCapabilityResponseSchema")


@_attrs_define
class ComposioProviderCapabilityResponseSchema:
    """
    Attributes:
        toolkit_slug (str):
        auth_config_schema (ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset):
        auth_scheme (AuthScheme | Unset):
        provider (Literal['COMPOSIO'] | Unset):  Default: 'COMPOSIO'.
        supports_org_custom_auth_config (bool | Unset):  Default: False.
        system_default_available (bool | Unset):  Default: True.
    """

    toolkit_slug: str
    auth_config_schema: (
        ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset
    ) = UNSET
    auth_scheme: AuthScheme | Unset = UNSET
    provider: Literal["COMPOSIO"] | Unset = "COMPOSIO"
    supports_org_custom_auth_config: bool | Unset = False
    system_default_available: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.composio_provider_capability_response_schema_auth_config_schema_type_0 import (
            ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        )

        toolkit_slug = self.toolkit_slug

        auth_config_schema: dict[str, Any] | None | Unset
        if isinstance(self.auth_config_schema, Unset):
            auth_config_schema = UNSET
        elif isinstance(
            self.auth_config_schema,
            ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        ):
            auth_config_schema = self.auth_config_schema.to_dict()
        else:
            auth_config_schema = self.auth_config_schema

        auth_scheme: str | Unset = UNSET
        if not isinstance(self.auth_scheme, Unset):
            auth_scheme = self.auth_scheme.value

        provider = self.provider

        supports_org_custom_auth_config = self.supports_org_custom_auth_config

        system_default_available = self.system_default_available

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "toolkit_slug": toolkit_slug,
            }
        )
        if auth_config_schema is not UNSET:
            field_dict["auth_config_schema"] = auth_config_schema
        if auth_scheme is not UNSET:
            field_dict["auth_scheme"] = auth_scheme
        if provider is not UNSET:
            field_dict["provider"] = provider
        if supports_org_custom_auth_config is not UNSET:
            field_dict["supports_org_custom_auth_config"] = (
                supports_org_custom_auth_config
            )
        if system_default_available is not UNSET:
            field_dict["system_default_available"] = system_default_available

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.composio_provider_capability_response_schema_auth_config_schema_type_0 import (
            ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0,
        )

        d = dict(src_dict)
        toolkit_slug = d.pop("toolkit_slug")

        def _parse_auth_config_schema(
            data: object,
        ) -> (
            ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0 | None | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                auth_config_schema_type_0 = ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0.from_dict(
                    data
                )

                return auth_config_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                ComposioProviderCapabilityResponseSchemaAuthConfigSchemaType0
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

        provider = cast(Literal["COMPOSIO"] | Unset, d.pop("provider", UNSET))
        if provider != "COMPOSIO" and not isinstance(provider, Unset):
            raise ValueError(f"provider must match const 'COMPOSIO', got '{provider}'")

        supports_org_custom_auth_config = d.pop(
            "supports_org_custom_auth_config", UNSET
        )

        system_default_available = d.pop("system_default_available", UNSET)

        composio_provider_capability_response_schema = cls(
            toolkit_slug=toolkit_slug,
            auth_config_schema=auth_config_schema,
            auth_scheme=auth_scheme,
            provider=provider,
            supports_org_custom_auth_config=supports_org_custom_auth_config,
            system_default_available=system_default_available,
        )

        composio_provider_capability_response_schema.additional_properties = d
        return composio_provider_capability_response_schema

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
