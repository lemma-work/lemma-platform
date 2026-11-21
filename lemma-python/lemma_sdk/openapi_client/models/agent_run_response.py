from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_run_status import AgentRunStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_token_usage import RunTokenUsage


T = TypeVar("T", bound="AgentRunResponse")


@_attrs_define
class AgentRunResponse:
    """One run of a conversation: how it went, and the model tokens it used.

    Attributes:
        conversation_id (UUID):
        id (UUID):
        started_at (datetime.datetime):
        status (AgentRunStatus): Internal lifecycle for one harness execution.
        agent_id (None | Unset | UUID):
        error (None | str | Unset):
        finished_at (datetime.datetime | None | Unset):
        output (Any | None | Unset):
        parent_run_id (None | Unset | UUID):
        usage (None | RunTokenUsage | Unset): Model tokens the run used, summed over its model calls; null when none
            were recorded against it. A run still working reads what it has used so far. Leaves out the conversation's title
            and the memory kept after the run, which land later. Tokens only: no cost.
    """

    conversation_id: UUID
    id: UUID
    started_at: datetime.datetime
    status: AgentRunStatus
    agent_id: None | Unset | UUID = UNSET
    error: None | str | Unset = UNSET
    finished_at: datetime.datetime | None | Unset = UNSET
    output: Any | None | Unset = UNSET
    parent_run_id: None | Unset | UUID = UNSET
    usage: None | RunTokenUsage | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.run_token_usage import RunTokenUsage

        conversation_id = str(self.conversation_id)

        id = str(self.id)

        started_at = self.started_at.isoformat()

        status = self.status.value

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        finished_at: None | str | Unset
        if isinstance(self.finished_at, Unset):
            finished_at = UNSET
        elif isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        output: Any | None | Unset
        if isinstance(self.output, Unset):
            output = UNSET
        else:
            output = self.output

        parent_run_id: None | str | Unset
        if isinstance(self.parent_run_id, Unset):
            parent_run_id = UNSET
        elif isinstance(self.parent_run_id, UUID):
            parent_run_id = str(self.parent_run_id)
        else:
            parent_run_id = self.parent_run_id

        usage: dict[str, Any] | None | Unset
        if isinstance(self.usage, Unset):
            usage = UNSET
        elif isinstance(self.usage, RunTokenUsage):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "id": id,
                "started_at": started_at,
                "status": status,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if error is not UNSET:
            field_dict["error"] = error
        if finished_at is not UNSET:
            field_dict["finished_at"] = finished_at
        if output is not UNSET:
            field_dict["output"] = output
        if parent_run_id is not UNSET:
            field_dict["parent_run_id"] = parent_run_id
        if usage is not UNSET:
            field_dict["usage"] = usage

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_token_usage import RunTokenUsage

        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        id = UUID(d.pop("id"))

        started_at = isoparse(d.pop("started_at"))

        status = AgentRunStatus(d.pop("status"))

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

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_finished_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = isoparse(data)

                return finished_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        finished_at = _parse_finished_at(d.pop("finished_at", UNSET))

        def _parse_output(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        output = _parse_output(d.pop("output", UNSET))

        def _parse_parent_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                parent_run_id_type_0 = UUID(data)

                return parent_run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        parent_run_id = _parse_parent_run_id(d.pop("parent_run_id", UNSET))

        def _parse_usage(data: object) -> None | RunTokenUsage | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = RunTokenUsage.from_dict(data)

                return usage_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | RunTokenUsage | Unset, data)

        usage = _parse_usage(d.pop("usage", UNSET))

        agent_run_response = cls(
            conversation_id=conversation_id,
            id=id,
            started_at=started_at,
            status=status,
            agent_id=agent_id,
            error=error,
            finished_at=finished_at,
            output=output,
            parent_run_id=parent_run_id,
            usage=usage,
        )

        agent_run_response.additional_properties = d
        return agent_run_response

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
