from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.agent_run_approval_decision import AgentRunApprovalDecision
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.resolve_user_approval_request_response_type_0 import (
        ResolveUserApprovalRequestResponseType0,
    )


T = TypeVar("T", bound="ResolveUserApprovalRequest")


@_attrs_define
class ResolveUserApprovalRequest:
    """
    Attributes:
        decision (AgentRunApprovalDecision): User decision scope for a run approval.
        response (None | ResolveUserApprovalRequestResponseType0 | Unset):
    """

    decision: AgentRunApprovalDecision
    response: None | ResolveUserApprovalRequestResponseType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.resolve_user_approval_request_response_type_0 import (
            ResolveUserApprovalRequestResponseType0,
        )

        decision = self.decision.value

        response: dict[str, Any] | None | Unset
        if isinstance(self.response, Unset):
            response = UNSET
        elif isinstance(self.response, ResolveUserApprovalRequestResponseType0):
            response = self.response.to_dict()
        else:
            response = self.response

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "decision": decision,
            }
        )
        if response is not UNSET:
            field_dict["response"] = response

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.resolve_user_approval_request_response_type_0 import (
            ResolveUserApprovalRequestResponseType0,
        )

        d = dict(src_dict)
        decision = AgentRunApprovalDecision(d.pop("decision"))

        def _parse_response(
            data: object,
        ) -> None | ResolveUserApprovalRequestResponseType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_type_0 = ResolveUserApprovalRequestResponseType0.from_dict(
                    data
                )

                return response_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ResolveUserApprovalRequestResponseType0 | Unset, data)

        response = _parse_response(d.pop("response", UNSET))

        resolve_user_approval_request = cls(
            decision=decision,
            response=response,
        )

        resolve_user_approval_request.additional_properties = d
        return resolve_user_approval_request

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
