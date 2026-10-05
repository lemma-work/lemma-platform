from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workflow_run_wait_type import WorkflowRunWaitType
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkflowRunWaitingOn")


@_attrs_define
class WorkflowRunWaitingOn:
    """Where a suspended run is parked and on whom -- the active wait, cut
    down to what a list needs. The full wait (form schema included) is on
    `workflow.run.get` as `active_wait`.

        Attributes:
            node_id (str):
            wait_type (WorkflowRunWaitType):
            assigned_pod_member_id (None | Unset | UUID): The pod member a FORM wait is assigned to, when it is.
            since (datetime.datetime | None | Unset): When the run started waiting here.
    """

    node_id: str
    wait_type: WorkflowRunWaitType
    assigned_pod_member_id: None | Unset | UUID = UNSET
    since: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        node_id = self.node_id

        wait_type = self.wait_type.value

        assigned_pod_member_id: None | str | Unset
        if isinstance(self.assigned_pod_member_id, Unset):
            assigned_pod_member_id = UNSET
        elif isinstance(self.assigned_pod_member_id, UUID):
            assigned_pod_member_id = str(self.assigned_pod_member_id)
        else:
            assigned_pod_member_id = self.assigned_pod_member_id

        since: None | str | Unset
        if isinstance(self.since, Unset):
            since = UNSET
        elif isinstance(self.since, datetime.datetime):
            since = self.since.isoformat()
        else:
            since = self.since

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "node_id": node_id,
                "wait_type": wait_type,
            }
        )
        if assigned_pod_member_id is not UNSET:
            field_dict["assigned_pod_member_id"] = assigned_pod_member_id
        if since is not UNSET:
            field_dict["since"] = since

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        node_id = d.pop("node_id")

        wait_type = WorkflowRunWaitType(d.pop("wait_type"))

        def _parse_assigned_pod_member_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assigned_pod_member_id_type_0 = UUID(data)

                return assigned_pod_member_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        assigned_pod_member_id = _parse_assigned_pod_member_id(
            d.pop("assigned_pod_member_id", UNSET)
        )

        def _parse_since(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                since_type_0 = isoparse(data)

                return since_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        since = _parse_since(d.pop("since", UNSET))

        workflow_run_waiting_on = cls(
            node_id=node_id,
            wait_type=wait_type,
            assigned_pod_member_id=assigned_pod_member_id,
            since=since,
        )

        workflow_run_waiting_on.additional_properties = d
        return workflow_run_waiting_on

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
