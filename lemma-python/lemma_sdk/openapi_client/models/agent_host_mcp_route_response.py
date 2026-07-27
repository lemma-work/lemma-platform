from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.agent_host_mcp_route_response_mcp import AgentHostMcpRouteResponseMcp


T = TypeVar("T", bound="AgentHostMcpRouteResponse")


@_attrs_define
class AgentHostMcpRouteResponse:
    """
    Attributes:
        expires_at (datetime.datetime):
        lease_epoch (int):
        mcp (AgentHostMcpRouteResponseMcp):
        route_id (UUID):
        run_id (UUID):
    """

    expires_at: datetime.datetime
    lease_epoch: int
    mcp: AgentHostMcpRouteResponseMcp
    route_id: UUID
    run_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires_at = self.expires_at.isoformat()

        lease_epoch = self.lease_epoch

        mcp = self.mcp.to_dict()

        route_id = str(self.route_id)

        run_id = str(self.run_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires_at": expires_at,
                "lease_epoch": lease_epoch,
                "mcp": mcp,
                "route_id": route_id,
                "run_id": run_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_mcp_route_response_mcp import (
            AgentHostMcpRouteResponseMcp,
        )

        d = dict(src_dict)
        expires_at = isoparse(d.pop("expires_at"))

        lease_epoch = d.pop("lease_epoch")

        mcp = AgentHostMcpRouteResponseMcp.from_dict(d.pop("mcp"))

        route_id = UUID(d.pop("route_id"))

        run_id = UUID(d.pop("run_id"))

        agent_host_mcp_route_response = cls(
            expires_at=expires_at,
            lease_epoch=lease_epoch,
            mcp=mcp,
            route_id=route_id,
            run_id=run_id,
        )

        agent_host_mcp_route_response.additional_properties = d
        return agent_host_mcp_route_response

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
