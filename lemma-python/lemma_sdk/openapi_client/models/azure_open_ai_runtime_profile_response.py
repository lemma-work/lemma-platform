from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.runtime_profile_scope import RuntimeProfileScope
from ..models.runtime_profile_status import RuntimeProfileStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.azure_open_ai_runtime_config import AzureOpenAIRuntimeConfig
    from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry


T = TypeVar("T", bound="AzureOpenAIRuntimeProfileResponse")


@_attrs_define
class AzureOpenAIRuntimeProfileResponse:
    """
    Attributes:
        config (AzureOpenAIRuntimeConfig | None):
        id (str):
        name (str):
        runtime_type (Literal['AZURE_OPENAI']):
        scope (RuntimeProfileScope):
        status (RuntimeProfileStatus):
        availability_status (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        has_credentials (bool | Unset):  Default: False.
        model_catalog (list[RuntimeModelCatalogEntry] | Unset):
        organization_id (None | Unset | UUID):
        owner_user_id (None | Unset | UUID):
    """

    config: AzureOpenAIRuntimeConfig | None
    id: str
    name: str
    runtime_type: Literal["AZURE_OPENAI"]
    scope: RuntimeProfileScope
    status: RuntimeProfileStatus
    availability_status: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    has_credentials: bool | Unset = False
    model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    owner_user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.azure_open_ai_runtime_config import AzureOpenAIRuntimeConfig

        config: dict[str, Any] | None
        if isinstance(self.config, AzureOpenAIRuntimeConfig):
            config = self.config.to_dict()
        else:
            config = self.config

        id = self.id

        name = self.name

        runtime_type = self.runtime_type

        scope = self.scope.value

        status = self.status.value

        availability_status: None | str | Unset
        if isinstance(self.availability_status, Unset):
            availability_status = UNSET
        else:
            availability_status = self.availability_status

        default_model_name: None | str | Unset
        if isinstance(self.default_model_name, Unset):
            default_model_name = UNSET
        else:
            default_model_name = self.default_model_name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        has_credentials = self.has_credentials

        model_catalog: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.model_catalog, Unset):
            model_catalog = []
            for model_catalog_item_data in self.model_catalog:
                model_catalog_item = model_catalog_item_data.to_dict()
                model_catalog.append(model_catalog_item)

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

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
                "config": config,
                "id": id,
                "name": name,
                "runtime_type": runtime_type,
                "scope": scope,
                "status": status,
            }
        )
        if availability_status is not UNSET:
            field_dict["availability_status"] = availability_status
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if has_credentials is not UNSET:
            field_dict["has_credentials"] = has_credentials
        if model_catalog is not UNSET:
            field_dict["model_catalog"] = model_catalog
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if owner_user_id is not UNSET:
            field_dict["owner_user_id"] = owner_user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.azure_open_ai_runtime_config import AzureOpenAIRuntimeConfig
        from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry

        d = dict(src_dict)

        def _parse_config(data: object) -> AzureOpenAIRuntimeConfig | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = AzureOpenAIRuntimeConfig.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AzureOpenAIRuntimeConfig | None, data)

        config = _parse_config(d.pop("config"))

        id = d.pop("id")

        name = d.pop("name")

        runtime_type = cast(Literal["AZURE_OPENAI"], d.pop("runtime_type"))
        if runtime_type != "AZURE_OPENAI":
            raise ValueError(
                f"runtime_type must match const 'AZURE_OPENAI', got '{runtime_type}'"
            )

        scope = RuntimeProfileScope(d.pop("scope"))

        status = RuntimeProfileStatus(d.pop("status"))

        def _parse_availability_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        availability_status = _parse_availability_status(
            d.pop("availability_status", UNSET)
        )

        def _parse_default_model_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_model_name = _parse_default_model_name(
            d.pop("default_model_name", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        has_credentials = d.pop("has_credentials", UNSET)

        _model_catalog = d.pop("model_catalog", UNSET)
        model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
        if _model_catalog is not UNSET:
            model_catalog = []
            for model_catalog_item_data in _model_catalog:
                model_catalog_item = RuntimeModelCatalogEntry.from_dict(
                    model_catalog_item_data
                )

                model_catalog.append(model_catalog_item)

        def _parse_organization_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

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

        azure_open_ai_runtime_profile_response = cls(
            config=config,
            id=id,
            name=name,
            runtime_type=runtime_type,
            scope=scope,
            status=status,
            availability_status=availability_status,
            default_model_name=default_model_name,
            description=description,
            has_credentials=has_credentials,
            model_catalog=model_catalog,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
        )

        azure_open_ai_runtime_profile_response.additional_properties = d
        return azure_open_ai_runtime_profile_response

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
