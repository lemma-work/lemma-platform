from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workflow_mode import WorkflowMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_node_response import AgentNodeResponse
    from ..models.data_store_workflow_start_output import DataStoreWorkflowStartOutput
    from ..models.decision_node_response import DecisionNodeResponse
    from ..models.end_node_response import EndNodeResponse
    from ..models.event_workflow_start_output import EventWorkflowStartOutput
    from ..models.form_node_response import FormNodeResponse
    from ..models.function_node_response import FunctionNodeResponse
    from ..models.loop_node_response import LoopNodeResponse
    from ..models.manual_workflow_start_output import ManualWorkflowStartOutput
    from ..models.scheduled_workflow_start_output import ScheduledWorkflowStartOutput
    from ..models.wait_until_node_response import WaitUntilNodeResponse
    from ..models.workflow_edge import WorkflowEdge


T = TypeVar("T", bound="WorkflowDetailResponse")


@_attrs_define
class WorkflowDetailResponse:
    """
    Attributes:
        id (UUID):
        name (str):
        pod_id (UUID):
        allowed_actions (list[str] | Unset):
        created_at (datetime.datetime | None | Unset):
        description (None | str | Unset):
        edges (list[WorkflowEdge] | Unset):
        icon_url (None | str | Unset):
        is_active (bool | Unset):  Default: True.
        mode (WorkflowMode | Unset): Workflow schedule ownership mode.
        nodes (list[AgentNodeResponse | DecisionNodeResponse | EndNodeResponse | FormNodeResponse | FunctionNodeResponse
            | LoopNodeResponse | WaitUntilNodeResponse] | Unset):
        start (DataStoreWorkflowStartOutput | EventWorkflowStartOutput | ManualWorkflowStartOutput | None |
            ScheduledWorkflowStartOutput | Unset):
        updated_at (datetime.datetime | None | Unset):
        visibility (str | Unset):  Default: 'POD'.
    """

    id: UUID
    name: str
    pod_id: UUID
    allowed_actions: list[str] | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    description: None | str | Unset = UNSET
    edges: list[WorkflowEdge] | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    is_active: bool | Unset = True
    mode: WorkflowMode | Unset = UNSET
    nodes: (
        list[
            AgentNodeResponse
            | DecisionNodeResponse
            | EndNodeResponse
            | FormNodeResponse
            | FunctionNodeResponse
            | LoopNodeResponse
            | WaitUntilNodeResponse
        ]
        | Unset
    ) = UNSET
    start: (
        DataStoreWorkflowStartOutput
        | EventWorkflowStartOutput
        | ManualWorkflowStartOutput
        | None
        | ScheduledWorkflowStartOutput
        | Unset
    ) = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    visibility: str | Unset = "POD"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_node_response import AgentNodeResponse
        from ..models.data_store_workflow_start_output import (
            DataStoreWorkflowStartOutput,
        )
        from ..models.decision_node_response import DecisionNodeResponse
        from ..models.event_workflow_start_output import EventWorkflowStartOutput
        from ..models.form_node_response import FormNodeResponse
        from ..models.function_node_response import FunctionNodeResponse
        from ..models.loop_node_response import LoopNodeResponse
        from ..models.manual_workflow_start_output import ManualWorkflowStartOutput
        from ..models.scheduled_workflow_start_output import (
            ScheduledWorkflowStartOutput,
        )
        from ..models.wait_until_node_response import WaitUntilNodeResponse

        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        edges: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.edges, Unset):
            edges = []
            for edges_item_data in self.edges:
                edges_item = edges_item_data.to_dict()
                edges.append(edges_item)

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        is_active = self.is_active

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        nodes: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.nodes, Unset):
            nodes = []
            for nodes_item_data in self.nodes:
                nodes_item: dict[str, Any]
                if isinstance(nodes_item_data, FormNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, AgentNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, FunctionNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, DecisionNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, LoopNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, WaitUntilNodeResponse):
                    nodes_item = nodes_item_data.to_dict()
                else:
                    nodes_item = nodes_item_data.to_dict()

                nodes.append(nodes_item)

        start: dict[str, Any] | None | Unset
        if isinstance(self.start, Unset):
            start = UNSET
        elif isinstance(self.start, ManualWorkflowStartOutput):
            start = self.start.to_dict()
        elif isinstance(self.start, ScheduledWorkflowStartOutput):
            start = self.start.to_dict()
        elif isinstance(self.start, EventWorkflowStartOutput):
            start = self.start.to_dict()
        elif isinstance(self.start, DataStoreWorkflowStartOutput):
            start = self.start.to_dict()
        else:
            start = self.start

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "pod_id": pod_id,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if description is not UNSET:
            field_dict["description"] = description
        if edges is not UNSET:
            field_dict["edges"] = edges
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if is_active is not UNSET:
            field_dict["is_active"] = is_active
        if mode is not UNSET:
            field_dict["mode"] = mode
        if nodes is not UNSET:
            field_dict["nodes"] = nodes
        if start is not UNSET:
            field_dict["start"] = start
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_node_response import AgentNodeResponse
        from ..models.data_store_workflow_start_output import (
            DataStoreWorkflowStartOutput,
        )
        from ..models.decision_node_response import DecisionNodeResponse
        from ..models.end_node_response import EndNodeResponse
        from ..models.event_workflow_start_output import EventWorkflowStartOutput
        from ..models.form_node_response import FormNodeResponse
        from ..models.function_node_response import FunctionNodeResponse
        from ..models.loop_node_response import LoopNodeResponse
        from ..models.manual_workflow_start_output import ManualWorkflowStartOutput
        from ..models.scheduled_workflow_start_output import (
            ScheduledWorkflowStartOutput,
        )
        from ..models.wait_until_node_response import WaitUntilNodeResponse
        from ..models.workflow_edge import WorkflowEdge

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        _edges = d.pop("edges", UNSET)
        edges: list[WorkflowEdge] | Unset = UNSET
        if _edges is not UNSET:
            edges = []
            for edges_item_data in _edges:
                edges_item = WorkflowEdge.from_dict(edges_item_data)

                edges.append(edges_item)

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        is_active = d.pop("is_active", UNSET)

        _mode = d.pop("mode", UNSET)
        mode: WorkflowMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = WorkflowMode(_mode)

        _nodes = d.pop("nodes", UNSET)
        nodes: (
            list[
                AgentNodeResponse
                | DecisionNodeResponse
                | EndNodeResponse
                | FormNodeResponse
                | FunctionNodeResponse
                | LoopNodeResponse
                | WaitUntilNodeResponse
            ]
            | Unset
        ) = UNSET
        if _nodes is not UNSET:
            nodes = []
            for nodes_item_data in _nodes:

                def _parse_nodes_item(
                    data: object,
                ) -> (
                    AgentNodeResponse
                    | DecisionNodeResponse
                    | EndNodeResponse
                    | FormNodeResponse
                    | FunctionNodeResponse
                    | LoopNodeResponse
                    | WaitUntilNodeResponse
                ):
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_0 = FormNodeResponse.from_dict(data)

                        return nodes_item_type_0
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_1 = AgentNodeResponse.from_dict(data)

                        return nodes_item_type_1
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_2 = FunctionNodeResponse.from_dict(data)

                        return nodes_item_type_2
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_3 = DecisionNodeResponse.from_dict(data)

                        return nodes_item_type_3
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_4 = LoopNodeResponse.from_dict(data)

                        return nodes_item_type_4
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_5 = WaitUntilNodeResponse.from_dict(data)

                        return nodes_item_type_5
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    nodes_item_type_6 = EndNodeResponse.from_dict(data)

                    return nodes_item_type_6

                nodes_item = _parse_nodes_item(nodes_item_data)

                nodes.append(nodes_item)

        def _parse_start(
            data: object,
        ) -> (
            DataStoreWorkflowStartOutput
            | EventWorkflowStartOutput
            | ManualWorkflowStartOutput
            | None
            | ScheduledWorkflowStartOutput
            | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_0 = ManualWorkflowStartOutput.from_dict(data)

                return start_type_0_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_1 = ScheduledWorkflowStartOutput.from_dict(data)

                return start_type_0_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_2 = EventWorkflowStartOutput.from_dict(data)

                return start_type_0_type_2
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_3 = DataStoreWorkflowStartOutput.from_dict(data)

                return start_type_0_type_3
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(
                DataStoreWorkflowStartOutput
                | EventWorkflowStartOutput
                | ManualWorkflowStartOutput
                | None
                | ScheduledWorkflowStartOutput
                | Unset,
                data,
            )

        start = _parse_start(d.pop("start", UNSET))

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = isoparse(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        visibility = d.pop("visibility", UNSET)

        workflow_detail_response = cls(
            id=id,
            name=name,
            pod_id=pod_id,
            allowed_actions=allowed_actions,
            created_at=created_at,
            description=description,
            edges=edges,
            icon_url=icon_url,
            is_active=is_active,
            mode=mode,
            nodes=nodes,
            start=start,
            updated_at=updated_at,
            visibility=visibility,
        )

        workflow_detail_response.additional_properties = d
        return workflow_detail_response

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
