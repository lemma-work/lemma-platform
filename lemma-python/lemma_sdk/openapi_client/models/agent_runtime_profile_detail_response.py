from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_host_status import AgentHostStatus
from ..models.harness_kind import HarnessKind
from ..models.runtime_profile_kind import RuntimeProfileKind
from ..models.runtime_profile_protocol import RuntimeProfileProtocol
from ..models.runtime_profile_scope import RuntimeProfileScope
from ..models.runtime_profile_status import RuntimeProfileStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_harness_response import AgentHostHarnessResponse
    from ..models.agent_runtime_profile_detail_response_config import (
        AgentRuntimeProfileDetailResponseConfig,
    )
    from ..models.agent_runtime_profile_detail_response_metadata import (
        AgentRuntimeProfileDetailResponseMetadata,
    )
    from ..models.runtime_model_catalog_entry import RuntimeModelCatalogEntry


T = TypeVar("T", bound="AgentRuntimeProfileDetailResponse")


@_attrs_define
class AgentRuntimeProfileDetailResponse:
    """One profile plus the live harness it is bound to.

    An editor has to render the harness's *current* config options, not the ones
    the profile was saved against - those are what the edit will be validated
    and re-pinned to.

        Attributes:
            derived_harness_kind (HarnessKind): Runtime framework used to execute an agent.

                Two kinds, not one per coding tool: ``LEMMA`` runs in-process, ``HARNESS``
                dispatches through Agent Host. Which tool Agent Host runs is identified by
                ``harness_id`` on the runtime profile, so the retired per-tool values
                (``CODEX``, ``CLAUDE_CODE``, ``OPENCODE``, ``CURSOR``, ``ANTIGRAVITY``) went
                away with the local daemon that needed them.

                No stored row is read back through this enum — a persisted runtime profile
                names a ``RuntimeProfileProtocol``, and the kind is derived from that — so
                dropping those values cannot fail a history read. Back-compat for retired
                *protocols* is handled where it belongs, in the profile repository.
            id (str):
            kind (RuntimeProfileKind):
            name (str):
            protocol (RuntimeProfileProtocol): How a profile reaches its runtime.

                The retired local daemon needed one protocol per coding tool
                (``CODEX_APP_SERVER``, ``CLAUDE_CODE``, ``OPENCODE``, ``CURSOR``,
                ``ANTIGRAVITY``). Agent Host needs one: the tool is identified by the
                profile's ``harness_id``. Stored rows can still carry a retired value, so
                the profile repository skips protocols this enum no longer knows rather
                than failing the whole listing.
            scope (RuntimeProfileScope):
            status (RuntimeProfileStatus):
            availability_status (None | str | Unset):
            config (AgentRuntimeProfileDetailResponseConfig | Unset):
            default_model_name (None | str | Unset):
            description (None | str | Unset):
            harness (AgentHostHarnessResponse | None | Unset):
            harness_id (None | Unset | UUID):
            has_credentials (bool | Unset):  Default: False.
            host_status (AgentHostStatus | None | Unset):
            metadata (AgentRuntimeProfileDetailResponseMetadata | Unset):
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
    config: AgentRuntimeProfileDetailResponseConfig | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    harness: AgentHostHarnessResponse | None | Unset = UNSET
    harness_id: None | Unset | UUID = UNSET
    has_credentials: bool | Unset = False
    host_status: AgentHostStatus | None | Unset = UNSET
    metadata: AgentRuntimeProfileDetailResponseMetadata | Unset = UNSET
    model_catalog: list[RuntimeModelCatalogEntry] | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_host_harness_response import AgentHostHarnessResponse

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

        harness: dict[str, Any] | None | Unset
        if isinstance(self.harness, Unset):
            harness = UNSET
        elif isinstance(self.harness, AgentHostHarnessResponse):
            harness = self.harness.to_dict()
        else:
            harness = self.harness

        harness_id: None | str | Unset
        if isinstance(self.harness_id, Unset):
            harness_id = UNSET
        elif isinstance(self.harness_id, UUID):
            harness_id = str(self.harness_id)
        else:
            harness_id = self.harness_id

        has_credentials = self.has_credentials

        host_status: None | str | Unset
        if isinstance(self.host_status, Unset):
            host_status = UNSET
        elif isinstance(self.host_status, AgentHostStatus):
            host_status = self.host_status.value
        else:
            host_status = self.host_status

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
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if harness is not UNSET:
            field_dict["harness"] = harness
        if harness_id is not UNSET:
            field_dict["harness_id"] = harness_id
        if has_credentials is not UNSET:
            field_dict["has_credentials"] = has_credentials
        if host_status is not UNSET:
            field_dict["host_status"] = host_status
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
        from ..models.agent_host_harness_response import AgentHostHarnessResponse
        from ..models.agent_runtime_profile_detail_response_config import (
            AgentRuntimeProfileDetailResponseConfig,
        )
        from ..models.agent_runtime_profile_detail_response_metadata import (
            AgentRuntimeProfileDetailResponseMetadata,
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
        config: AgentRuntimeProfileDetailResponseConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = AgentRuntimeProfileDetailResponseConfig.from_dict(_config)

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

        def _parse_harness(data: object) -> AgentHostHarnessResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                harness_type_0 = AgentHostHarnessResponse.from_dict(data)

                return harness_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentHostHarnessResponse | None | Unset, data)

        harness = _parse_harness(d.pop("harness", UNSET))

        def _parse_harness_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                harness_id_type_0 = UUID(data)

                return harness_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        harness_id = _parse_harness_id(d.pop("harness_id", UNSET))

        has_credentials = d.pop("has_credentials", UNSET)

        def _parse_host_status(data: object) -> AgentHostStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                host_status_type_0 = AgentHostStatus(data)

                return host_status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentHostStatus | None | Unset, data)

        host_status = _parse_host_status(d.pop("host_status", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: AgentRuntimeProfileDetailResponseMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = AgentRuntimeProfileDetailResponseMetadata.from_dict(_metadata)

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

        agent_runtime_profile_detail_response = cls(
            derived_harness_kind=derived_harness_kind,
            id=id,
            kind=kind,
            name=name,
            protocol=protocol,
            scope=scope,
            status=status,
            availability_status=availability_status,
            config=config,
            default_model_name=default_model_name,
            description=description,
            harness=harness,
            harness_id=harness_id,
            has_credentials=has_credentials,
            host_status=host_status,
            metadata=metadata,
            model_catalog=model_catalog,
            organization_id=organization_id,
            user_id=user_id,
        )

        agent_runtime_profile_detail_response.additional_properties = d
        return agent_runtime_profile_detail_response

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
