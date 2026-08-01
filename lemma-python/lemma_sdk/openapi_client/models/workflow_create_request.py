from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_visibility import ResourceVisibility
from ..models.workflow_mode import WorkflowMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_node import AgentNode
    from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
    from ..models.decision_node import DecisionNode
    from ..models.end_node import EndNode
    from ..models.event_workflow_start_input import EventWorkflowStartInput
    from ..models.form_node import FormNode
    from ..models.function_node import FunctionNode
    from ..models.loop_node import LoopNode
    from ..models.manual_workflow_start_input import ManualWorkflowStartInput
    from ..models.notify_node import NotifyNode
    from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput
    from ..models.wait_until_node import WaitUntilNode
    from ..models.workflow_edge import WorkflowEdge


T = TypeVar("T", bound="WorkflowCreateRequest")


@_attrs_define
class WorkflowCreateRequest:
    """
    Attributes:
        name (str): Workflow name.
        description (None | str | Unset): Optional workflow description.
        edges (list[WorkflowEdge] | Unset): Optional initial graph edges connecting the provided nodes.
        icon_url (None | str | Unset): Optional public icon URL for the workflow.
        mode (WorkflowMode | Unset): Workflow schedule ownership mode.
        nodes (list[AgentNode | DecisionNode | EndNode | FormNode | FunctionNode | LoopNode | NotifyNode |
            WaitUntilNode] | Unset): Optional initial graph nodes. When provided, the graph is stored at creation time so a
            separate `workflow.graph.update` call is not required. Omit (or pass an empty list) to create a shell and upload
            the graph later. Node `input_mapping` entries must use explicit typed bindings like `{"type": "expression",
            "value": "start.payload.x"}`.
        start (DataStoreWorkflowStartInput | EventWorkflowStartInput | ManualWorkflowStartInput | None |
            ScheduledWorkflowStartInput | Unset): Start configuration. If omitted, the workflow can be started manually via
            `workflow.start`.
        visibility (ResourceVisibility | Unset):
    """

    name: str
    description: None | str | Unset = UNSET
    edges: list[WorkflowEdge] | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    mode: WorkflowMode | Unset = UNSET
    nodes: (
        list[
            AgentNode
            | DecisionNode
            | EndNode
            | FormNode
            | FunctionNode
            | LoopNode
            | NotifyNode
            | WaitUntilNode
        ]
        | Unset
    ) = UNSET
    start: (
        DataStoreWorkflowStartInput
        | EventWorkflowStartInput
        | ManualWorkflowStartInput
        | None
        | ScheduledWorkflowStartInput
        | Unset
    ) = UNSET
    visibility: ResourceVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_node import AgentNode
        from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
        from ..models.decision_node import DecisionNode
        from ..models.event_workflow_start_input import EventWorkflowStartInput
        from ..models.form_node import FormNode
        from ..models.function_node import FunctionNode
        from ..models.loop_node import LoopNode
        from ..models.manual_workflow_start_input import ManualWorkflowStartInput
        from ..models.notify_node import NotifyNode
        from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput
        from ..models.wait_until_node import WaitUntilNode

        name = self.name

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

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        nodes: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.nodes, Unset):
            nodes = []
            for nodes_item_data in self.nodes:
                nodes_item: dict[str, Any]
                if isinstance(nodes_item_data, FormNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, AgentNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, FunctionNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, DecisionNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, LoopNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, WaitUntilNode):
                    nodes_item = nodes_item_data.to_dict()
                elif isinstance(nodes_item_data, NotifyNode):
                    nodes_item = nodes_item_data.to_dict()
                else:
                    nodes_item = nodes_item_data.to_dict()

                nodes.append(nodes_item)

        start: dict[str, Any] | None | Unset
        if isinstance(self.start, Unset):
            start = UNSET
        elif isinstance(self.start, ManualWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, ScheduledWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, EventWorkflowStartInput):
            start = self.start.to_dict()
        elif isinstance(self.start, DataStoreWorkflowStartInput):
            start = self.start.to_dict()
        else:
            start = self.start

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if edges is not UNSET:
            field_dict["edges"] = edges
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if mode is not UNSET:
            field_dict["mode"] = mode
        if nodes is not UNSET:
            field_dict["nodes"] = nodes
        if start is not UNSET:
            field_dict["start"] = start
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_node import AgentNode
        from ..models.data_store_workflow_start_input import DataStoreWorkflowStartInput
        from ..models.decision_node import DecisionNode
        from ..models.end_node import EndNode
        from ..models.event_workflow_start_input import EventWorkflowStartInput
        from ..models.form_node import FormNode
        from ..models.function_node import FunctionNode
        from ..models.loop_node import LoopNode
        from ..models.manual_workflow_start_input import ManualWorkflowStartInput
        from ..models.notify_node import NotifyNode
        from ..models.scheduled_workflow_start_input import ScheduledWorkflowStartInput
        from ..models.wait_until_node import WaitUntilNode
        from ..models.workflow_edge import WorkflowEdge

        d = dict(src_dict)
        name = d.pop("name")

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

        _mode = d.pop("mode", UNSET)
        mode: WorkflowMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = WorkflowMode(_mode)

        _nodes = d.pop("nodes", UNSET)
        nodes: (
            list[
                AgentNode
                | DecisionNode
                | EndNode
                | FormNode
                | FunctionNode
                | LoopNode
                | NotifyNode
                | WaitUntilNode
            ]
            | Unset
        ) = UNSET
        if _nodes is not UNSET:
            nodes = []
            for nodes_item_data in _nodes:

                def _parse_nodes_item(
                    data: object,
                ) -> (
                    AgentNode
                    | DecisionNode
                    | EndNode
                    | FormNode
                    | FunctionNode
                    | LoopNode
                    | NotifyNode
                    | WaitUntilNode
                ):
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_0 = FormNode.from_dict(data)

                        return nodes_item_type_0
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_1 = AgentNode.from_dict(data)

                        return nodes_item_type_1
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_2 = FunctionNode.from_dict(data)

                        return nodes_item_type_2
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_3 = DecisionNode.from_dict(data)

                        return nodes_item_type_3
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_4 = LoopNode.from_dict(data)

                        return nodes_item_type_4
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_5 = WaitUntilNode.from_dict(data)

                        return nodes_item_type_5
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        nodes_item_type_6 = NotifyNode.from_dict(data)

                        return nodes_item_type_6
                    except TypeError, ValueError, AttributeError, KeyError:
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    nodes_item_type_7 = EndNode.from_dict(data)

                    return nodes_item_type_7

                nodes_item = _parse_nodes_item(nodes_item_data)

                nodes.append(nodes_item)

        def _parse_start(
            data: object,
        ) -> (
            DataStoreWorkflowStartInput
            | EventWorkflowStartInput
            | ManualWorkflowStartInput
            | None
            | ScheduledWorkflowStartInput
            | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_0 = ManualWorkflowStartInput.from_dict(data)

                return start_type_0_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_1 = ScheduledWorkflowStartInput.from_dict(data)

                return start_type_0_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_2 = EventWorkflowStartInput.from_dict(data)

                return start_type_0_type_2
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                start_type_0_type_3 = DataStoreWorkflowStartInput.from_dict(data)

                return start_type_0_type_3
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                DataStoreWorkflowStartInput
                | EventWorkflowStartInput
                | ManualWorkflowStartInput
                | None
                | ScheduledWorkflowStartInput
                | Unset,
                data,
            )

        start = _parse_start(d.pop("start", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: ResourceVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = ResourceVisibility(_visibility)

        workflow_create_request = cls(
            name=name,
            description=description,
            edges=edges,
            icon_url=icon_url,
            mode=mode,
            nodes=nodes,
            start=start,
            visibility=visibility,
        )

        workflow_create_request.additional_properties = d
        return workflow_create_request

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
