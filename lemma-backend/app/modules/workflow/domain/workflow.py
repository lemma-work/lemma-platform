"""Workflow aggregate: the workflow definition."""

from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import ResourceType
from app.core.domain.aggregate import AggregateRoot
from app.modules.workflow.domain.graph import WorkflowGraphValidator, WorkflowEdge
from app.modules.workflow.domain.nodes import WorkflowNode
from app.modules.workflow.domain.start import WorkflowStart


class WorkflowMode(str, Enum):
    """Workflow schedule ownership mode."""

    GLOBAL = "GLOBAL"
    USER = "USER"


class WorkflowEntity(AggregateRoot):
    """Workflow aggregate representing a workflow definition."""

    resource_type: ClassVar[ResourceType] = ResourceType.WORKFLOW

    name: str
    description: str | None = None
    icon_url: str | None = None
    pod_id: UUID
    user_id: UUID | None = None

    # Graph definition
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    # Computed and stored at save time by graph validation. None only for
    # shell workflows that have no graph yet.
    entry_node_id: str | None = None

    start: Optional[WorkflowStart] = None

    is_active: bool = True
    mode: WorkflowMode = WorkflowMode.GLOBAL
    visibility: str = "POD"
    allowed_actions: list[str] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate_graph(self) -> None:
        """Validate the graph and refresh entry_node_id. Raises
        GraphValidationError when invalid. No-op for empty shells."""
        if not self.nodes:
            self.entry_node_id = None
            return
        self.entry_node_id = WorkflowGraphValidator.validate(self.nodes, self.edges)

    def get_node(self, node_id: str) -> WorkflowNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise ValueError(f"Node {node_id} not found in Workflow {self.id}")

    def has_node(self, node_id: str) -> bool:
        return any(node.id == node_id for node in self.nodes)

    def next_after(self, node_id: str) -> str | None:
        """Target of the single outgoing edge, or None at chain end.

        Validation guarantees non-decision nodes have at most one outgoing
        edge; decision branching uses rule targets, not edges.
        """
        for edge in self.edges:
            if edge.source == node_id:
                return edge.target
        return None


class WorkflowSummaryEntity(BaseModel):
    """Lightweight workflow view for list responses.

    Carries cheap derived graph stats (``node_count``/``node_types``) instead of
    the full ``nodes``/``edges`` graph, so list endpoints avoid both per-node
    validation and shipping the whole graph per item.
    """

    id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    pod_id: UUID
    user_id: UUID | None = None
    is_active: bool = True
    mode: WorkflowMode = WorkflowMode.GLOBAL
    visibility: str = "POD"
    node_count: int = 0
    node_types: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowUpdateEntity(BaseModel):
    """Update payload for workflow metadata."""

    description: str | None = None
    icon_url: str | None = None
    mode: WorkflowMode | None = None
    start: WorkflowStart | None = None
    visibility: str | None = None
