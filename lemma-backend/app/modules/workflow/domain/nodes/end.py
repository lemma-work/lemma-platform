"""End node configuration."""

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.workflow.domain.nodes.base import BaseNode, NodeType


class EndNodeConfig(BaseModel):
    """Configuration for End node."""


class EndNode(BaseNode):
    """End node. Completes the run."""

    type: Literal[NodeType.END] = NodeType.END
    config: EndNodeConfig = Field(default_factory=EndNodeConfig)
