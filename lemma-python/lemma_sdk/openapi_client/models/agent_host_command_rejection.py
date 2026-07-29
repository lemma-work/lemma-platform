from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_host_rejection_code import AgentHostRejectionCode
from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentHostCommandRejection")


@_attrs_define
class AgentHostCommandRejection:
    """
    Attributes:
        code (AgentHostRejectionCode):
        command_id (UUID):
        lease_epoch (int):
        retryable (bool):
        run_id (UUID):
        detail (None | str | Unset):
    """

    code: AgentHostRejectionCode
    command_id: UUID
    lease_epoch: int
    retryable: bool
    run_id: UUID
    detail: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code = self.code.value

        command_id = str(self.command_id)

        lease_epoch = self.lease_epoch

        retryable = self.retryable

        run_id = str(self.run_id)

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "command_id": command_id,
                "lease_epoch": lease_epoch,
                "retryable": retryable,
                "run_id": run_id,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        code = AgentHostRejectionCode(d.pop("code"))

        command_id = UUID(d.pop("command_id"))

        lease_epoch = d.pop("lease_epoch")

        retryable = d.pop("retryable")

        run_id = UUID(d.pop("run_id"))

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        agent_host_command_rejection = cls(
            code=code,
            command_id=command_id,
            lease_epoch=lease_epoch,
            retryable=retryable,
            run_id=run_id,
            detail=detail,
        )

        agent_host_command_rejection.additional_properties = d
        return agent_host_command_rejection

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
