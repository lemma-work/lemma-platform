from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentRunStartResponse")


@_attrs_define
class AgentRunStartResponse:
    """
    Attributes:
        agent_run_id (UUID):
        conversation_id (UUID):
        started_new_run (bool):
        deduplicated (bool | Unset): True when this send was already received -- the same client_message_id, or the same
            text from the same person a moment ago. Nothing new was stored; agent_run_id is the run already answering it.
            Default: False.
    """

    agent_run_id: UUID
    conversation_id: UUID
    started_new_run: bool
    deduplicated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_run_id = str(self.agent_run_id)

        conversation_id = str(self.conversation_id)

        started_new_run = self.started_new_run

        deduplicated = self.deduplicated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_run_id": agent_run_id,
                "conversation_id": conversation_id,
                "started_new_run": started_new_run,
            }
        )
        if deduplicated is not UNSET:
            field_dict["deduplicated"] = deduplicated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_run_id = UUID(d.pop("agent_run_id"))

        conversation_id = UUID(d.pop("conversation_id"))

        started_new_run = d.pop("started_new_run")

        deduplicated = d.pop("deduplicated", UNSET)

        agent_run_start_response = cls(
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            started_new_run=started_new_run,
            deduplicated=deduplicated,
        )

        agent_run_start_response.additional_properties = d
        return agent_run_start_response

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
