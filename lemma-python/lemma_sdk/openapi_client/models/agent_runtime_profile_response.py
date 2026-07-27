from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.harness_kind import HarnessKind
from ..models.runtime_profile_kind import RuntimeProfileKind
from ..models.runtime_profile_protocol import RuntimeProfileProtocol
from ..models.runtime_profile_scope import RuntimeProfileScope
from ..models.runtime_profile_status import RuntimeProfileStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_profile_response_config import (
        AgentRuntimeProfileResponseConfig,
    )
    from ..models.agent_runtime_profile_response_metadata import (
        AgentRuntimeProfileResponseMetadata,
    )
    from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry


T = TypeVar("T", bound="AgentRuntimeProfileResponse")


@_attrs_define
class AgentRuntimeProfileResponse:
    """
    Attributes:
        derived_harness_kind (HarnessKind): Runtime framework used to execute an agent.
        id (str):
        kind (RuntimeProfileKind):
        name (str):
        protocol (RuntimeProfileProtocol):
        scope (RuntimeProfileScope):
        status (RuntimeProfileStatus):
        availability_status (None | str | Unset):
        config (AgentRuntimeProfileResponseConfig | Unset):
        daemon_display_name (None | str | Unset):
        daemon_harness_available (bool | None | Unset):
        daemon_id (None | Unset | UUID):
        daemon_status (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        has_credentials (bool | Unset):  Default: False.
        host_display_name (None | str | Unset):
        host_id (None | Unset | UUID):
        host_integration_id (None | Unset | UUID):
        host_status (None | str | Unset):
        integration_config_revision (None | str | Unset):
        integration_health (None | str | Unset):
        integration_key (None | str | Unset):
        metadata (AgentRuntimeProfileResponseMetadata | Unset):
        model_catalog (list[RuntimeModelCatalogEntry] | Unset):
        organization_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
    """

    derived_harness_kind: HarnessKind
    id: str
    kind: RuntimeProfileKind
    name: str
    protocol: RuntimeProfileProtocol
    scope: RuntimeProfileScope
    status: RuntimeProfileStatus
    availability_status: None | str | Unset = UNSET
    config: AgentRuntimeProfileResponseConfig | Unset = UNSET
    daemon_display_name: None | str | Unset = UNSET
    daemon_harness_available: bool | None | Unset = UNSET
    daemon_id: None | Unset | UUID = UNSET
    daemon_status: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    has_credentials: bool | Unset = False
    host_display_name: None | str | Unset = UNSET
    host_id: None | Unset | UUID = UNSET
    host_integration_id: None | Unset | UUID = UNSET
    host_status: None | str | Unset = UNSET
    integration_config_revision: None | str | Unset = UNSET
    integration_health: None | str | Unset = UNSET
    integration_key: None | str | Unset = UNSET
    metadata: AgentRuntimeProfileResponseMetadata | Unset = UNSET
    model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        derived_harness_kind = self.derived_harness_kind.value

        id = self.id

        kind = self.kind.value

        name = self.name

        protocol = self.protocol.value

        scope = self.scope.value

        status = self.status.value

        availability_status: None | str | Unset
        if isinstance(self.availability_status, Unset):
            availability_status = UNSET
        else:
            availability_status = self.availability_status

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        daemon_display_name: None | str | Unset
        if isinstance(self.daemon_display_name, Unset):
            daemon_display_name = UNSET
        else:
            daemon_display_name = self.daemon_display_name

        daemon_harness_available: bool | None | Unset
        if isinstance(self.daemon_harness_available, Unset):
            daemon_harness_available = UNSET
        else:
            daemon_harness_available = self.daemon_harness_available

        daemon_id: None | str | Unset
        if isinstance(self.daemon_id, Unset):
            daemon_id = UNSET
        elif isinstance(self.daemon_id, UUID):
            daemon_id = str(self.daemon_id)
        else:
            daemon_id = self.daemon_id

        daemon_status: None | str | Unset
        if isinstance(self.daemon_status, Unset):
            daemon_status = UNSET
        else:
            daemon_status = self.daemon_status

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

        host_display_name: None | str | Unset
        if isinstance(self.host_display_name, Unset):
            host_display_name = UNSET
        else:
            host_display_name = self.host_display_name

        host_id: None | str | Unset
        if isinstance(self.host_id, Unset):
            host_id = UNSET
        elif isinstance(self.host_id, UUID):
            host_id = str(self.host_id)
        else:
            host_id = self.host_id

        host_integration_id: None | str | Unset
        if isinstance(self.host_integration_id, Unset):
            host_integration_id = UNSET
        elif isinstance(self.host_integration_id, UUID):
            host_integration_id = str(self.host_integration_id)
        else:
            host_integration_id = self.host_integration_id

        host_status: None | str | Unset
        if isinstance(self.host_status, Unset):
            host_status = UNSET
        else:
            host_status = self.host_status

        integration_config_revision: None | str | Unset
        if isinstance(self.integration_config_revision, Unset):
            integration_config_revision = UNSET
        else:
            integration_config_revision = self.integration_config_revision

        integration_health: None | str | Unset
        if isinstance(self.integration_health, Unset):
            integration_health = UNSET
        else:
            integration_health = self.integration_health

        integration_key: None | str | Unset
        if isinstance(self.integration_key, Unset):
            integration_key = UNSET
        else:
            integration_key = self.integration_key

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

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

        user_id: None | str | Unset
        if isinstance(self.user_id, Unset):
            user_id = UNSET
        elif isinstance(self.user_id, UUID):
            user_id = str(self.user_id)
        else:
            user_id = self.user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "derived_harness_kind": derived_harness_kind,
                "id": id,
                "kind": kind,
                "name": name,
                "protocol": protocol,
                "scope": scope,
                "status": status,
            }
        )
        if availability_status is not UNSET:
            field_dict["availability_status"] = availability_status
        if config is not UNSET:
            field_dict["config"] = config
        if daemon_display_name is not UNSET:
            field_dict["daemon_display_name"] = daemon_display_name
        if daemon_harness_available is not UNSET:
            field_dict["daemon_harness_available"] = daemon_harness_available
        if daemon_id is not UNSET:
            field_dict["daemon_id"] = daemon_id
        if daemon_status is not UNSET:
            field_dict["daemon_status"] = daemon_status
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if has_credentials is not UNSET:
            field_dict["has_credentials"] = has_credentials
        if host_display_name is not UNSET:
            field_dict["host_display_name"] = host_display_name
        if host_id is not UNSET:
            field_dict["host_id"] = host_id
        if host_integration_id is not UNSET:
            field_dict["host_integration_id"] = host_integration_id
        if host_status is not UNSET:
            field_dict["host_status"] = host_status
        if integration_config_revision is not UNSET:
            field_dict["integration_config_revision"] = integration_config_revision
        if integration_health is not UNSET:
            field_dict["integration_health"] = integration_health
        if integration_key is not UNSET:
            field_dict["integration_key"] = integration_key
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if model_catalog is not UNSET:
            field_dict["model_catalog"] = model_catalog
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if user_id is not UNSET:
            field_dict["user_id"] = user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_profile_response_config import (
            AgentRuntimeProfileResponseConfig,
        )
        from ..models.agent_runtime_profile_response_metadata import (
            AgentRuntimeProfileResponseMetadata,
        )
        from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry

        d = dict(src_dict)
        derived_harness_kind = HarnessKind(d.pop("derived_harness_kind"))

        id = d.pop("id")

        kind = RuntimeProfileKind(d.pop("kind"))

        name = d.pop("name")

        protocol = RuntimeProfileProtocol(d.pop("protocol"))

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

        _config = d.pop("config", UNSET)
        config: AgentRuntimeProfileResponseConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = AgentRuntimeProfileResponseConfig.from_dict(_config)

        def _parse_daemon_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        daemon_display_name = _parse_daemon_display_name(
            d.pop("daemon_display_name", UNSET)
        )

        def _parse_daemon_harness_available(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        daemon_harness_available = _parse_daemon_harness_available(
            d.pop("daemon_harness_available", UNSET)
        )

        def _parse_daemon_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                daemon_id_type_0 = UUID(data)

                return daemon_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        daemon_id = _parse_daemon_id(d.pop("daemon_id", UNSET))

        def _parse_daemon_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        daemon_status = _parse_daemon_status(d.pop("daemon_status", UNSET))

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

        def _parse_host_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        host_display_name = _parse_host_display_name(d.pop("host_display_name", UNSET))

        def _parse_host_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                host_id_type_0 = UUID(data)

                return host_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        host_id = _parse_host_id(d.pop("host_id", UNSET))

        def _parse_host_integration_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                host_integration_id_type_0 = UUID(data)

                return host_integration_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        host_integration_id = _parse_host_integration_id(
            d.pop("host_integration_id", UNSET)
        )

        def _parse_host_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        host_status = _parse_host_status(d.pop("host_status", UNSET))

        def _parse_integration_config_revision(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        integration_config_revision = _parse_integration_config_revision(
            d.pop("integration_config_revision", UNSET)
        )

        def _parse_integration_health(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        integration_health = _parse_integration_health(
            d.pop("integration_health", UNSET)
        )

        def _parse_integration_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        integration_key = _parse_integration_key(d.pop("integration_key", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: AgentRuntimeProfileResponseMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = AgentRuntimeProfileResponseMetadata.from_dict(_metadata)

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

        def _parse_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                user_id_type_0 = UUID(data)

                return user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        user_id = _parse_user_id(d.pop("user_id", UNSET))

        agent_runtime_profile_response = cls(
            derived_harness_kind=derived_harness_kind,
            id=id,
            kind=kind,
            name=name,
            protocol=protocol,
            scope=scope,
            status=status,
            availability_status=availability_status,
            config=config,
            daemon_display_name=daemon_display_name,
            daemon_harness_available=daemon_harness_available,
            daemon_id=daemon_id,
            daemon_status=daemon_status,
            default_model_name=default_model_name,
            description=description,
            has_credentials=has_credentials,
            host_display_name=host_display_name,
            host_id=host_id,
            host_integration_id=host_integration_id,
            host_status=host_status,
            integration_config_revision=integration_config_revision,
            integration_health=integration_health,
            integration_key=integration_key,
            metadata=metadata,
            model_catalog=model_catalog,
            organization_id=organization_id,
            user_id=user_id,
        )

        agent_runtime_profile_response.additional_properties = d
        return agent_runtime_profile_response

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
