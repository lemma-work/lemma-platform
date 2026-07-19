from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_run_status import AgentRunStatus
from ..models.conversation_status import ConversationStatus
from ..models.conversation_type import ConversationType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.conversation_response_metadata_type_0 import (
        ConversationResponseMetadataType0,
    )


T = TypeVar("T", bound="ConversationResponse")


@_attrs_define
class ConversationResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        pod_id (UUID):
        updated_at (datetime.datetime):
        user_id (UUID):
        agent_id (None | Unset | UUID):
        agent_runtime (AgentRuntimeConfig | None | Unset):
        instructions (None | str | Unset):
        last_run_error (None | str | Unset):
        last_run_finished_at (datetime.datetime | None | Unset):
        last_run_retryable (bool | Unset):  Default: False.
        last_run_status (AgentRunStatus | None | Unset):
        metadata (ConversationResponseMetadataType0 | None | Unset):
        organization_id (None | Unset | UUID):
        output (Any | None | Unset):
        parent_id (None | Unset | UUID):
        status (ConversationStatus | None | Unset):
        title (None | str | Unset):
        type_ (ConversationType | Unset): User-visible conversation behavior.
    """

    created_at: datetime.datetime
    id: UUID
    pod_id: UUID
    updated_at: datetime.datetime
    user_id: UUID
    agent_id: None | Unset | UUID = UNSET
    agent_runtime: AgentRuntimeConfig | None | Unset = UNSET
    instructions: None | str | Unset = UNSET
    last_run_error: None | str | Unset = UNSET
    last_run_finished_at: datetime.datetime | None | Unset = UNSET
    last_run_retryable: bool | Unset = False
    last_run_status: AgentRunStatus | None | Unset = UNSET
    metadata: ConversationResponseMetadataType0 | None | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    output: Any | None | Unset = UNSET
    parent_id: None | Unset | UUID = UNSET
    status: ConversationStatus | None | Unset = UNSET
    title: None | str | Unset = UNSET
    type_: ConversationType | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.conversation_response_metadata_type_0 import (
            ConversationResponseMetadataType0,
        )

        created_at = self.created_at.isoformat()

        id = str(self.id)

        pod_id = str(self.pod_id)

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        agent_runtime: dict[str, Any] | None | Unset
        if isinstance(self.agent_runtime, Unset):
            agent_runtime = UNSET
        elif isinstance(self.agent_runtime, AgentRuntimeConfig):
            agent_runtime = self.agent_runtime.to_dict()
        else:
            agent_runtime = self.agent_runtime

        instructions: None | str | Unset
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        last_run_error: None | str | Unset
        if isinstance(self.last_run_error, Unset):
            last_run_error = UNSET
        else:
            last_run_error = self.last_run_error

        last_run_finished_at: None | str | Unset
        if isinstance(self.last_run_finished_at, Unset):
            last_run_finished_at = UNSET
        elif isinstance(self.last_run_finished_at, datetime.datetime):
            last_run_finished_at = self.last_run_finished_at.isoformat()
        else:
            last_run_finished_at = self.last_run_finished_at

        last_run_retryable = self.last_run_retryable

        last_run_status: None | str | Unset
        if isinstance(self.last_run_status, Unset):
            last_run_status = UNSET
        elif isinstance(self.last_run_status, AgentRunStatus):
            last_run_status = self.last_run_status.value
        else:
            last_run_status = self.last_run_status

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, ConversationResponseMetadataType0):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        output: Any | None | Unset
        if isinstance(self.output, Unset):
            output = UNSET
        else:
            output = self.output

        parent_id: None | str | Unset
        if isinstance(self.parent_id, Unset):
            parent_id = UNSET
        elif isinstance(self.parent_id, UUID):
            parent_id = str(self.parent_id)
        else:
            parent_id = self.parent_id

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, ConversationStatus):
            status = self.status.value
        else:
            status = self.status

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        type_: str | Unset = UNSET
        if not isinstance(self.type_, Unset):
            type_ = self.type_.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "pod_id": pod_id,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_runtime is not UNSET:
            field_dict["agent_runtime"] = agent_runtime
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if last_run_error is not UNSET:
            field_dict["last_run_error"] = last_run_error
        if last_run_finished_at is not UNSET:
            field_dict["last_run_finished_at"] = last_run_finished_at
        if last_run_retryable is not UNSET:
            field_dict["last_run_retryable"] = last_run_retryable
        if last_run_status is not UNSET:
            field_dict["last_run_status"] = last_run_status
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if output is not UNSET:
            field_dict["output"] = output
        if parent_id is not UNSET:
            field_dict["parent_id"] = parent_id
        if status is not UNSET:
            field_dict["status"] = status
        if title is not UNSET:
            field_dict["title"] = title
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.conversation_response_metadata_type_0 import (
            ConversationResponseMetadataType0,
        )

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        pod_id = UUID(d.pop("pod_id"))

        updated_at = isoparse(d.pop("updated_at"))

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
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_agent_runtime(data: object) -> AgentRuntimeConfig | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                agent_runtime_type_0 = AgentRuntimeConfig.from_dict(data)

                return agent_runtime_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentRuntimeConfig | None | Unset, data)

        agent_runtime = _parse_agent_runtime(d.pop("agent_runtime", UNSET))

        def _parse_instructions(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_last_run_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_run_error = _parse_last_run_error(d.pop("last_run_error", UNSET))

        def _parse_last_run_finished_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_finished_at_type_0 = isoparse(data)

                return last_run_finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_run_finished_at = _parse_last_run_finished_at(
            d.pop("last_run_finished_at", UNSET)
        )

        last_run_retryable = d.pop("last_run_retryable", UNSET)

        def _parse_last_run_status(data: object) -> AgentRunStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_status_type_0 = AgentRunStatus(data)

                return last_run_status_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentRunStatus | None | Unset, data)

        last_run_status = _parse_last_run_status(d.pop("last_run_status", UNSET))

        def _parse_metadata(
            data: object,
        ) -> ConversationResponseMetadataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = ConversationResponseMetadataType0.from_dict(data)

                return metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConversationResponseMetadataType0 | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

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
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

        def _parse_output(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        output = _parse_output(d.pop("output", UNSET))

        def _parse_parent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                parent_id_type_0 = UUID(data)

                return parent_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        parent_id = _parse_parent_id(d.pop("parent_id", UNSET))

        def _parse_status(data: object) -> ConversationStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = ConversationStatus(data)

                return status_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConversationStatus | None | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        _type_ = d.pop("type", UNSET)
        type_: ConversationType | Unset
        if isinstance(_type_, Unset):
            type_ = UNSET
        else:
            type_ = ConversationType(_type_)

        conversation_response = cls(
            created_at=created_at,
            id=id,
            pod_id=pod_id,
            updated_at=updated_at,
            user_id=user_id,
            agent_id=agent_id,
            agent_runtime=agent_runtime,
            instructions=instructions,
            last_run_error=last_run_error,
            last_run_finished_at=last_run_finished_at,
            last_run_retryable=last_run_retryable,
            last_run_status=last_run_status,
            metadata=metadata,
            organization_id=organization_id,
            output=output,
            parent_id=parent_id,
            status=status,
            title=title,
            type_=type_,
        )

        conversation_response.additional_properties = d
        return conversation_response

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
