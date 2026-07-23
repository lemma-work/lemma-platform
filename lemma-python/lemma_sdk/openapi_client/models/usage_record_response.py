from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.usage_record_response_metadata import UsageRecordResponseMetadata


T = TypeVar("T", bound="UsageRecordResponse")


@_attrs_define
class UsageRecordResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        input_tokens (int):
        metadata (UsageRecordResponseMetadata):
        model_name (str):
        occurred_at (datetime.datetime):
        output_tokens (int):
        profile_id (str):
        profile_scope (str):
        source_type (str):
        total_tokens (int):
        units (float):
        usage_kind (str):
        user_id (UUID):
        agent_id (None | Unset | UUID):
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):
        cost_usd (float | None | Unset):
        organization_id (None | Unset | UUID):
        parent_agent_run_id (None | Unset | UUID):
        pod_id (None | Unset | UUID):
        source_id (None | str | Unset):
        status (None | str | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    input_tokens: int
    metadata: UsageRecordResponseMetadata
    model_name: str
    occurred_at: datetime.datetime
    output_tokens: int
    profile_id: str
    profile_scope: str
    source_type: str
    total_tokens: int
    units: float
    usage_kind: str
    user_id: UUID
    agent_id: None | Unset | UUID = UNSET
    agent_run_id: None | Unset | UUID = UNSET
    conversation_id: None | Unset | UUID = UNSET
    cost_usd: float | None | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    parent_agent_run_id: None | Unset | UUID = UNSET
    pod_id: None | Unset | UUID = UNSET
    source_id: None | str | Unset = UNSET
    status: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        input_tokens = self.input_tokens

        metadata = self.metadata.to_dict()

        model_name = self.model_name

        occurred_at = self.occurred_at.isoformat()

        output_tokens = self.output_tokens

        profile_id = self.profile_id

        profile_scope = self.profile_scope

        source_type = self.source_type

        total_tokens = self.total_tokens

        units = self.units

        usage_kind = self.usage_kind

        user_id = str(self.user_id)

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        agent_run_id: None | str | Unset
        if isinstance(self.agent_run_id, Unset):
            agent_run_id = UNSET
        elif isinstance(self.agent_run_id, UUID):
            agent_run_id = str(self.agent_run_id)
        else:
            agent_run_id = self.agent_run_id

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        cost_usd: float | None | Unset
        if isinstance(self.cost_usd, Unset):
            cost_usd = UNSET
        else:
            cost_usd = self.cost_usd

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        parent_agent_run_id: None | str | Unset
        if isinstance(self.parent_agent_run_id, Unset):
            parent_agent_run_id = UNSET
        elif isinstance(self.parent_agent_run_id, UUID):
            parent_agent_run_id = str(self.parent_agent_run_id)
        else:
            parent_agent_run_id = self.parent_agent_run_id

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        else:
            source_id = self.source_id

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        else:
            status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "input_tokens": input_tokens,
                "metadata": metadata,
                "model_name": model_name,
                "occurred_at": occurred_at,
                "output_tokens": output_tokens,
                "profile_id": profile_id,
                "profile_scope": profile_scope,
                "source_type": source_type,
                "total_tokens": total_tokens,
                "units": units,
                "usage_kind": usage_kind,
                "user_id": user_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_run_id is not UNSET:
            field_dict["agent_run_id"] = agent_run_id
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if cost_usd is not UNSET:
            field_dict["cost_usd"] = cost_usd
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if parent_agent_run_id is not UNSET:
            field_dict["parent_agent_run_id"] = parent_agent_run_id
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if source_id is not UNSET:
            field_dict["source_id"] = source_id
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.usage_record_response_metadata import UsageRecordResponseMetadata

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        input_tokens = d.pop("input_tokens")

        metadata = UsageRecordResponseMetadata.from_dict(d.pop("metadata"))

        model_name = d.pop("model_name")

        occurred_at = isoparse(d.pop("occurred_at"))

        output_tokens = d.pop("output_tokens")

        profile_id = d.pop("profile_id")

        profile_scope = d.pop("profile_scope")

        source_type = d.pop("source_type")

        total_tokens = d.pop("total_tokens")

        units = d.pop("units")

        usage_kind = d.pop("usage_kind")

        user_id = UUID(d.pop("user_id"))

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_agent_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_run_id_type_0 = UUID(data)

                return agent_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_run_id = _parse_agent_run_id(d.pop("agent_run_id", UNSET))

        def _parse_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        def _parse_cost_usd(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        cost_usd = _parse_cost_usd(d.pop("cost_usd", UNSET))

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

        def _parse_parent_agent_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                parent_agent_run_id_type_0 = UUID(data)

                return parent_agent_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        parent_agent_run_id = _parse_parent_agent_run_id(
            d.pop("parent_agent_run_id", UNSET)
        )

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        def _parse_source_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        def _parse_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        usage_record_response = cls(
            created_at=created_at,
            id=id,
            input_tokens=input_tokens,
            metadata=metadata,
            model_name=model_name,
            occurred_at=occurred_at,
            output_tokens=output_tokens,
            profile_id=profile_id,
            profile_scope=profile_scope,
            source_type=source_type,
            total_tokens=total_tokens,
            units=units,
            usage_kind=usage_kind,
            user_id=user_id,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            cost_usd=cost_usd,
            organization_id=organization_id,
            parent_agent_run_id=parent_agent_run_id,
            pod_id=pod_id,
            source_id=source_id,
            status=status,
        )

        usage_record_response.additional_properties = d
        return usage_record_response

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
