from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_host_run_state import AgentHostRunState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_run_checkpoint_detail import AgentHostRunCheckpointDetail


T = TypeVar("T", bound="AgentHostRunCheckpoint")


@_attrs_define
class AgentHostRunCheckpoint:
    """
    Attributes:
        lease_epoch (int):
        run_id (UUID):
        state (AgentHostRunState):
        detail (AgentHostRunCheckpointDetail | Unset):
    """

    lease_epoch: int
    run_id: UUID
    state: AgentHostRunState
    detail: AgentHostRunCheckpointDetail | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        lease_epoch = self.lease_epoch

        run_id = str(self.run_id)

        state = self.state.value

        detail: dict[str, Any] | Unset = UNSET
        if not isinstance(self.detail, Unset):
            detail = self.detail.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "lease_epoch": lease_epoch,
                "run_id": run_id,
                "state": state,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_run_checkpoint_detail import (
            AgentHostRunCheckpointDetail,
        )

        d = dict(src_dict)
        lease_epoch = d.pop("lease_epoch")

        run_id = UUID(d.pop("run_id"))

        state = AgentHostRunState(d.pop("state"))

        _detail = d.pop("detail", UNSET)
        detail: AgentHostRunCheckpointDetail | Unset
        if isinstance(_detail, Unset):
            detail = UNSET
        else:
            detail = AgentHostRunCheckpointDetail.from_dict(_detail)

        agent_host_run_checkpoint = cls(
            lease_epoch=lease_epoch,
            run_id=run_id,
            state=state,
            detail=detail,
        )

        agent_host_run_checkpoint.additional_properties = d
        return agent_host_run_checkpoint

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
