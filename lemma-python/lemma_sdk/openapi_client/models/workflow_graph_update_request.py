from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

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


T = TypeVar("T", bound="WorkflowGraphUpdateRequest")


@_attrs_define
class WorkflowGraphUpdateRequest:
    """Named request body for replacing a workflow graph.

    Example:
        {'edges': [], 'nodes': [{'config': {'function_name': 'summarize-ticket', 'input_mapping': {'channel': {'type':
            'literal', 'value': 'support'}, 'ticket_key': {'type': 'expression', 'value': 'start.payload.ticket.key'}}},
            'id': 'collect_context', 'type': 'FUNCTION'}], 'start': {'config': {'operations': ['INSERT', 'UPDATE',
            'DELETE'], 'table_name': 'expenses'}, 'type': 'DATASTORE_EVENT'}}

    Attributes:
        edges (list[WorkflowEdge]): Complete edge list connecting the provided nodes.
        nodes (list[AgentNode | DecisionNode | EndNode | FormNode | FunctionNode | LoopNode | NotifyNode |
            WaitUntilNode]): Complete node list for the workflow graph. Agent/function `input_mapping` entries must use
            explicit typed bindings like `{"type": "expression", "value": "start.payload.issue.key"}` or `{"type":
            "literal", "value": "finance"}`.
        start (DataStoreWorkflowStartInput | EventWorkflowStartInput | ManualWorkflowStartInput | None |
            ScheduledWorkflowStartInput | Unset): Optional replacement start configuration stored with the graph.
    """

    edges: list[WorkflowEdge]
    nodes: list[
        AgentNode
        | DecisionNode
        | EndNode
        | FormNode
        | FunctionNode
        | LoopNode
        | NotifyNode
        | WaitUntilNode
    ]
    start: (
        DataStoreWorkflowStartInput
        | EventWorkflowStartInput
        | ManualWorkflowStartInput
        | None
        | ScheduledWorkflowStartInput
        | Unset
    ) = UNSET
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

        edges = []
        for edges_item_data in self.edges:
            edges_item = edges_item_data.to_dict()
            edges.append(edges_item)

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

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "edges": edges,
                "nodes": nodes,
            }
        )
        if start is not UNSET:
            field_dict["start"] = start

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
        edges = []
        _edges = d.pop("edges")
        for edges_item_data in _edges:
            edges_item = WorkflowEdge.from_dict(edges_item_data)

            edges.append(edges_item)

        nodes = []
        _nodes = d.pop("nodes")
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

        workflow_graph_update_request = cls(
            edges=edges,
            nodes=nodes,
            start=start,
        )

        workflow_graph_update_request.additional_properties = d
        return workflow_graph_update_request

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
